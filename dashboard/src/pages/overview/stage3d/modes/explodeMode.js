/**
 * 【结构拆图】视图模式 —— 爆炸视图（F1 实现，2026-10-02 二版）。
 *
 * 行为概要：
 * - enter：以整机世界包围盒中心为原点，为每个语义部件动态求解"爆炸方向 + 距离"
 *   （轴向组沿整机最长边两侧抽出、Stator 上移、Bedplate 下沉、外向组外抬），
 *   爆炸系数 0→1 easeInOutCubic 平滑推开；
 *   同时**相机自动拉远**：按爆炸后整机包围盒 + 当前视口 fov/长宽比精确计算
 *   "全部部件入画"的距离（1.25 安全边距），保持当前观察方向动画推到位——
 *   任何屏幕尺寸/窗口比例下拆开后全部部件都在所见范围内（用户硬性要求）。
 * - exit：**合成动画**（用户要求补齐）——部件从当前位置（可能拆到一半）与相机一起
 *   easeInOutCubic 合拢回整机位姿与原取景，通过引擎级 addTween 注册（不受模式
 *   切换的 frameFns 清理影响），动画结束才把位姿/锚点交还框架兜底复位。
 * - updateFrame：node.position = home.position + localDir * (dist * k)，并同步
 *   part.anchor = homeAnchor + worldDir * (dist * k)（卡片每帧按 anchor 重投影）。
 *   世界方向经"父链世界矩阵去平移取逆"折算到部件父空间，天然兼容根节点 ×12 缩放。
 * - 悬停/选中高亮仍由框架托管，本模式不碰材质。
 * - equipment_map 增删部件时未知 id 走 outUp 兜底角色，方向依然动态求解。
 */
import * as THREE from 'three'

/* ───────── 纯视觉常量（不承载业务语义，允许常量化） ───────── */

/** 爆炸/合成共用过渡时长（秒） */
const EXPLODE_DURATION = 0.8
const COMBINE_DURATION = 0.5
/** 方向分量判零阈值 */
const DIR_EPS = 1e-6
/** 外向组"上抬"分量（合成方向前叠加的 Y 分量） */
const OUTUP_LIFT = 0.65
/** 拆图相机取景安全边距（整机拆开后全部入画的关键） */
const EXPLODE_FIT_MARGIN = 1.25

/** 角色 → 距离因子（× 整机最长边）。收紧后的适中拆分；最终是否出屏由相机自适应取景兜底 */
const ROLE_DIST = {
  axial: 0.26, // 转子（沿轴向抽出）
  axialLead: 0.5, // 主轴/联轴器（与转子同轴，距离更远，视觉分层）
  bearing: 0.4, // 两端轴承（轴向两侧，越过转子端部）
  up: 0.22, // 定子（上移）
  down: 0.2, // 底座（下沉）
  outUp: 0.28, // 冷却/润滑/接线柜（外向 + 上抬）
}

/** equipment_map 部件 id → 爆炸角色（未知 id 兜底 outUp） */
const ROLE_BY_ID = {
  rotor: 'axial',
  shaft: 'axialLead',
  bearing_de: 'bearing',
  bearing_nde: 'bearing',
  stator: 'up',
  bedplate: 'down',
  cooling_system: 'outUp',
  lubrication_system: 'outUp',
  terminal_box: 'outUp',
}

/** easeInOutCubic：先缓入后缓出，拆图开合都有"起手-收势"感 */
function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2
}

/* ───────── enter 期一次性使用的临时向量/矩阵（enter 不在渲染热路径） ───────── */
const _box = new THREE.Box3()
const _center = new THREE.Vector3()
const _size = new THREE.Vector3()
const _pc = new THREE.Vector3()
const _off = new THREE.Vector3()
const _dir = new THREE.Vector3()
const _v = new THREE.Vector3()
const _mat = new THREE.Matrix4()

/**
 * 预计算爆炸计划：每部件一条
 * { part, dist, worldDir(单位,世界系), localDir(父空间,已含缩放折算), homeAnchor,
 *   startPos/startAnchor（合成动画起点，exit 时捕获） }
 * 之后逐帧只做 copy/addScaledVector，零分配。
 */
function buildPlan(ctx) {
  const model = ctx.model
  if (!model) return []
  model.updateMatrixWorld(true) // 确保父链世界矩阵最新（供父空间折算用）

  _box.setFromObject(model)
  if (_box.isEmpty()) return []
  _box.getCenter(_center)
  _box.getSize(_size)
  const refLen = Math.max(_size.x, _size.y, _size.z) || 1
  // 轴向 = 整机最长边方向（动态推导；equipment_map 标注 shaftAxis=X，本机型即 X）
  const axisIdx = _size.x >= _size.y && _size.x >= _size.z ? 0 : _size.y >= _size.z ? 1 : 2

  const plan = []
  ctx.parts.forEach((part) => {
    if (!part?.node || !part.home) return // 无节点的部件无法移动，锚点保持原位
    const role = ROLE_BY_ID[part.id] || 'outUp'

    // 部件世界中心（空包围盒退回锚点），求相对整机中心的偏移
    _box.setFromObject(part.node)
    if (_box.isEmpty()) _pc.copy(part.anchor)
    else _box.getCenter(_pc)
    _off.subVectors(_pc, _center)

    // 按角色求世界方向（单位向量）
    switch (role) {
      case 'axial':
      case 'axialLead':
      case 'bearing': {
        // 沿轴向抽出，方向取部件中心相对整机中心的符号（两侧自然分开）
        const c = _off.getComponent(axisIdx)
        const s = c > DIR_EPS ? 1 : c < -DIR_EPS ? -1 : 1
        _dir.set(0, 0, 0).setComponent(axisIdx, s)
        break
      }
      case 'up':
        _dir.set(0, 1, 0)
        break
      case 'down':
        _dir.set(0, -1, 0)
        break
      default: {
        // outUp：水平外向为主，叠加固定上抬分量再归一
        _dir.set(_off.x, 0, _off.z)
        if (_dir.lengthSq() < DIR_EPS * DIR_EPS) _dir.set(0, 0, 1)
        else _dir.normalize()
        _dir.y = OUTUP_LIFT
        _dir.normalize()
      }
    }

    // 世界方向 → 部件父空间：父链世界矩阵去平移取线性部分求逆
    // （localDir 长度自动含 1/缩放，localDir * 世界距离 即父空间位移）
    let lx = _dir.x
    let ly = _dir.y
    let lz = _dir.z
    if (part.node.parent) {
      _mat.copy(part.node.parent.matrixWorld)
      _mat.setPosition(0, 0, 0)
      _mat.invert()
      _v.set(lx, ly, lz).applyMatrix4(_mat)
      lx = _v.x
      ly = _v.y
      lz = _v.z
    }

    plan.push({
      part,
      dist: refLen * (ROLE_DIST[role] ?? ROLE_DIST.outUp),
      worldDir: _dir.clone(),
      localDir: new THREE.Vector3(lx, ly, lz),
      homeAnchor: part.anchor.clone(),
    })
  })
  return plan
}

/** 按爆炸系数 k∈[0,1] 应用位移：位姿走 localDir，锚点走 worldDir（同一世界距离） */
function applyFactor(plan, k) {
  for (let i = 0; i < plan.length; i += 1) {
    const it = plan[i]
    const d = it.dist * k
    const { node, home, anchor } = it.part
    node.position.copy(home.position).addScaledVector(it.localDir, d)
    anchor.copy(it.homeAnchor).addScaledVector(it.worldDir, d)
  }
}

/**
 * 拆图态自适应取景：按"爆炸后整机包围盒 + 当前视口 fov/长宽比"计算
 * 全部部件入画所需的相机距离（保持当前观察方向），任何屏幕尺寸都成立。
 * 返回 { camEnd, targetEnd }；计算期间会临时应用 k=1 再复原。
 */
function measureExplodedFit(ctx, plan) {
  applyFactor(plan, 1)
  const model = ctx.model
  model.updateMatrixWorld(true)
  _box.setFromObject(model)
  applyFactor(plan, 0)
  if (_box.isEmpty()) return null
  _box.getCenter(_center)
  _box.getSize(_size)

  const cam = ctx.camera
  const fov = THREE.MathUtils.degToRad(cam.fov || 40)
  const aspect = Math.max(cam.aspect || 1, 0.1)
  const distH = _size.y / 2 / Math.tan(fov / 2)
  const distW = _size.x / 2 / (Math.tan(fov / 2) * aspect)
  const dist = Math.max(distH, distW) * EXPLODE_FIT_MARGIN + _size.z * 0.1

  // 保持当前观察方向，从爆炸中心退到入画距离
  _dir.subVectors(cam.position, ctx.controls.target)
  if (_dir.lengthSq() < DIR_EPS * DIR_EPS) _dir.set(0.78, 0.52, 1)
  _dir.normalize()

  return {
    camEnd: _center.clone().addScaledVector(_dir, dist),
    targetEnd: _center.clone(),
  }
}

/* ───────── 模式内动画状态（模块级；enter 重建、exit 清空，切换自愈） ───────── */
let plan = []
let elapsed = 0
let playing = false
let camStart = null // 进入拆图前的相机位姿（退出合成时回到这里）
let targetStart = null
let camEnd = null // 拆图态自适应取景的相机终点
let targetEnd = null
let combineCancel = null // 合成动画的取消函数（重进拆图时先取消旧合成）

/** 立即还原：锚点归位（框架 resetPartsTransform 不覆盖锚点）+ 清空动画状态 */
function cleanup() {
  playing = false
  for (let i = 0; i < plan.length; i += 1) {
    const it = plan[i]
    if (it.part?.anchor && it.homeAnchor) it.part.anchor.copy(it.homeAnchor)
  }
  plan = []
}

export default {
  id: 'explode',
  label: '结构拆图',

  enter(ctx) {
    // 若上一次合成动画还在飞，先取消，避免与本次拆图互相改位置
    if (combineCancel) {
      combineCancel()
      combineCancel = null
    }
    plan = buildPlan(ctx)
    if (!plan.length) return
    elapsed = 0
    playing = true
    applyFactor(plan, 0) // 起始帧先回到整机位姿，动画从系数 0 开始

    // 相机动画终点：拆图态自适应取景（进入前先记住原取景，退出合成时回去）
    camStart = ctx.camera.position.clone()
    targetStart = ctx.controls.target.clone()
    const fit = measureExplodedFit(ctx, plan)
    camEnd = fit ? fit.camEnd : ctx.camera.position.clone()
    targetEnd = fit ? fit.targetEnd : ctx.controls.target.clone()
  },

  /** 拆图态不消费通道数据（基础色/悬停/选中高亮均由框架与其他模式托管） */
  update() {},

  /** 每帧推进：爆炸系数 0→1 + 相机从原取景动画到拆图取景（同步 easing） */
  updateFrame(ctx, dt) {
    if (!playing || !camEnd) return
    elapsed += dt
    const k = Math.min(elapsed / EXPLODE_DURATION, 1)
    const e = easeInOutCubic(k)
    applyFactor(plan, e)
    ctx.camera.position.lerpVectors(camStart, camEnd, e)
    ctx.controls.target.lerpVectors(targetStart, targetEnd, e)
    if (k >= 1) playing = false
  },

  /**
   * 退出拆图：合成动画（用户要求补齐）。
   * 引擎 setMode 在 exit 返回后会立刻把部件复位到 home——这里先把"当前 exploded
   * 位姿 → home/原取景"注册成引擎级补间（addTween 不受 frameFns 清理影响），
   * 下一帧起部件与相机一起 easeInOutCubic 合拢 0.5s，收势后交还框架兜底。
   */
  exit(ctx) {
    const cancel = combineCancel
    if (cancel) cancel()
    combineCancel = null

    // 捕获合成起点：部件当前位姿（可能拆到一半）与当前相机
    const items = plan.map((it) => ({
      part: it.part,
      startPos: it.part.node ? it.part.node.position.clone() : null,
      startAnchor: it.part.anchor.clone(),
      homePos: it.part.home ? it.part.home.position.clone() : null,
      homeAnchor: it.homeAnchor.clone(),
    }))
    const camFrom = ctx.camera.position.clone()
    const tgtFrom = ctx.controls.target.clone()

    cleanup() // 清内部计划；位姿交由下面的合成补间接管，收势后框架兜底

    if (!items.length || typeof ctx.addTween !== 'function') return // 无可动画内容/旧契约兜底

    let t = 0
    combineCancel = ctx.addTween((dt) => {
      t += dt / COMBINE_DURATION
      const e = easeInOutCubic(Math.min(t, 1))
      for (let i = 0; i < items.length; i += 1) {
        const it = items[i]
        if (!it.startPos || !it.homePos) continue
        it.part.node.position.lerpVectors(it.startPos, it.homePos, e)
        it.part.anchor.lerpVectors(it.startAnchor, it.homeAnchor, e)
      }
      ctx.camera.position.lerpVectors(camFrom, camStart, e)
      ctx.controls.target.lerpVectors(tgtFrom, targetStart, e)
      if (t >= 1) {
        for (let i = 0; i < items.length; i += 1) {
          const it = items[i]
          if (it.homePos) it.part.node.position.copy(it.homePos)
          it.part.anchor.copy(it.homeAnchor)
        }
        combineCancel = null
        return false // 结束补间
      }
      return true
    })
  },

  dispose() {
    if (combineCancel) {
      combineCancel()
      combineCancel = null
    }
    cleanup()
  },
}
