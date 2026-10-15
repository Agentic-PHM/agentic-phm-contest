/**
 * StageEngine —— 3D 数字孪生舞台的 three.js 引擎层（非 React）。
 *
 * 职责：场景/相机/灯光/控制器、GLB(Draco) 加载与兜底、9 个语义部件管理
 * （逐 mesh 材质克隆 → 悬停/选中/诊断结论/基础色四态高亮）、部件标注卡片每帧重投影、
 * 模式（modes/）生命周期调度、完整 dispose。
 *
 * 交互参考实现：D:\TSI\tools_glb_viewer\verify.html。
 * 两条关键经验来自它：
 *  1) GLB 全部件共享同一材质实例，高亮前必须逐 mesh clone() 材质，否则整机一起变色；
 *  2) DRACOLoader 解码器已复制到 public/draco/（setDecoderPath('/draco/')）。
 */
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { MODE_MAP } from './modes/index.js'

const MODEL_URL = '/models/tiaoxiangji.glb'
const MAP_URL = '/models/equipment_map.json'
const DRACO_DECODER_PATH = '/draco/'
const HIGHLIGHT_HEX = 0x00c7ff // 与大屏主题色一致（AntD colorPrimary）
const SELECT_INTENSITY = 0.55
const HOVER_INTENSITY = 0.3
const DIAGNOSED_HEX = 0xff7a2a // 诊断结论联动：最新报告指向的部件（橙红）
const DIAGNOSED_INTENSITY = 0.4
const DEFAULT_TINT_INTENSITY = 0.22

/** equipment_map 全局缓存：页面反复挂载不重复 fetch */
let mapCache = null
function loadEquipmentMap() {
  if (!mapCache) {
    mapCache = fetch(MAP_URL).then((r) => {
      if (!r.ok) throw new Error(`equipment_map HTTP ${r.status}`)
      return r.json()
    })
    mapCache.catch(() => {
      mapCache = null // 失败允许下次重试
    })
  }
  return mapCache
}

/** 回调隔离：单个模式/帧回调抛错不能拖垮渲染循环 */
function safeCall(fn, ...args) {
  try {
    return fn(...args)
  } catch (e) {
    console.error('[Stage3D] callback error:', e)
  }
  return undefined
}

export class StageEngine {
  /**
   * @param {HTMLElement} host 舞台容器（canvas 将 append 进去）
   * @param {object} cb 回调集合：onProgress(0~1) / onReady({components,legendNote}) /
   *                    onError(err) / onHover(partId|null) / onSelect(partId|null) /
   *                    onProject(projectedItems)（每帧，卡片重投影）
   */
  constructor(host, cb = {}) {
    this.host = host
    this.cb = cb
    this.disposed = false
    this.ready = false
    this.modeId = null
    this.pendingModeId = null

    // ---- 渲染器：alpha 透明底，让 CSS 舞台（光晕/环台/网格）透出来 ----
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.domElement.classList.add('s3d-canvas')
    host.appendChild(this.renderer.domElement)

    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.05, 300)
    this.camera.position.set(14, 8, 16)

    // ---- 灯光：主光 + 冷色补光 + 半球环境光 + 底部青色地面补光（光晕感） ----
    const keyLight = new THREE.DirectionalLight(0xffffff, 1.6)
    keyLight.position.set(6, 10, 6)
    const fillLight = new THREE.DirectionalLight(0x9fc7ff, 0.5)
    fillLight.position.set(-8, 5, -7)
    const hemiLight = new THREE.HemisphereLight(0xbfd4ff, 0x30281a, 0.9)
    const underGlow = new THREE.PointLight(0x00c7ff, 60, 26, 2)
    underGlow.position.set(0, 0.6, 7)
    this.scene.add(keyLight, fillLight, hemiLight, underGlow)

    // 环境反射（verify.html 同款 RoomEnvironment，金属件高光层次）
    this.pmrem = new THREE.PMREMGenerator(this.renderer)
    this.scene.environment = this.pmrem.fromScene(new RoomEnvironment(), 0.04).texture

    // 底部光晕盘（与 CSS 光晕环叠加，增强"落地"感）
    this.glowDisc = this.buildGlowDisc()
    this.scene.add(this.glowDisc)

    // ---- 轨道控制器 ----
    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.minDistance = 3
    this.controls.maxDistance = 60
    this.controls.maxPolarAngle = Math.PI * 0.52 // 不允许转到地面以下

    // ---- 拾取/重投影复用对象（渲染循环内零新建） ----
    this.ray = new THREE.Raycaster()
    this.ndc = new THREE.Vector2()
    this.ndcDirty = false
    this.downPos = null
    this.tmpV = new THREE.Vector3()
    this.tmpV2 = new THREE.Vector3()
    this.tmpBox = new THREE.Box3()

    // ---- 数据/部件/模式状态 ----
    this.model = null
    this.equipmentMap = null
    this.parts = new Map() // partId -> PartHandle（结构见 buildParts）
    this.anchors = new Map() // partId -> Vector3（与 parts 中 anchor 同引用）
    this.partIdByMeshName = new Map()
    this.channels = null
    this.channelsByName = new Map()
    this.hoverId = null
    this.selectId = null
    this.diagnosedId = null
    this.activeMode = null
    this.frameFns = new Set()
    this.tweens = new Set() // 引擎级补间（不受模式切换清理影响）
    this.projected = []
    this.fitDist = 12 // fitCamera 后更新：卡片近大远小的参考距离

    this.ctx = this.buildCtx()
    this.bindEvents()
    this.onResize()

    this.ro = new ResizeObserver(() => this.onResize())
    this.ro.observe(host)

    this.clock = new THREE.Clock()
    this.tick = this.tick.bind(this)
    this.raf = requestAnimationFrame(this.tick)

    this.load()
  }

  /* ================= 对外 API（React 层调用） ================= */

  /** 轮询数据注入：卡片数值与模式着色共用这一份（channels 为 /api/channels 原始响应，可为 null） */
  setChannels(channels) {
    this.channels = channels
    this.channelsByName.clear()
    ;(channels?.channels || []).forEach((c) => this.channelsByName.set(c.name, c))
    if (this.activeMode && this.ready) safeCall(() => this.activeMode.update(this.ctx, channels))
  }

  /** 切换视图模式；未就绪时记录 pending，就绪后自动应用 */
  setMode(modeId) {
    if (!this.ready) {
      this.pendingModeId = modeId
      return
    }
    if (this.modeId === modeId) return
    this.modeId = modeId

    safeCall(() => this.activeMode?.exit?.(this.ctx))
    this.frameFns.clear() // onFrame 注册随模式切换自动清理
    this.setHover(null)
    this.setSelect(null)
    // 防御性复位：即使某个模式的 exit 有遗漏，下一个模式也拿到"干净"的整机
    this.resetPartsTransform()
    this.resetPartsTint()

    this.activeMode = MODE_MAP[modeId] || null
    if (this.activeMode) {
      safeCall(() => this.activeMode.enter(this.ctx))
      safeCall(() => this.activeMode.update(this.ctx, this.channels))
    }
  }

  /** 悬停指定部件（卡片 DOM 触发；画布悬停走内部 raycast，同一入口） */
  setHover(partId) {
    if (partId === this.hoverId) return
    const prev = this.hoverId ? this.parts.get(this.hoverId) : null
    const next = partId ? this.parts.get(partId) : null
    this.hoverId = partId || null
    if (prev) this.applyColor(prev)
    if (next) this.applyColor(next)
    this.cb.onHover?.(this.hoverId)
  }

  /** 选中部件（卡片/画布点击；null=取消，收起详情面板） */
  setSelect(partId) {
    if (partId === this.selectId) return
    const prev = this.selectId ? this.parts.get(this.selectId) : null
    const next = partId ? this.parts.get(partId) : null
    this.selectId = partId || null
    if (prev) this.applyColor(prev)
    if (next) this.applyColor(next)
    this.cb.onSelect?.(this.selectId)
  }

  /**
   * 诊断结论联动：设置最新报告指向的部件（橙红高亮，applyColor 层级在悬停之下、tint 之上）；null 清除。
   * 场景未就绪时只记录 id，buildParts 完成后会对命中部件补刷颜色。
   */
  setDiagnosedPart(partId) {
    if (partId === this.diagnosedId) return
    const prev = this.diagnosedId ? this.parts.get(this.diagnosedId) : null
    this.diagnosedId = partId || null
    const next = partId ? this.parts.get(partId) : null
    if (prev) this.applyColor(prev)
    if (next) this.applyColor(next)
  }

  /** 引擎级补间：跨模式动画（如拆图合成）用；step(dt) 返回 false 即结束。返回取消函数 */
  addTween(step) {
    const t = { step }
    this.tweens.add(t)
    return () => this.tweens.delete(t)
  }

  /** 设置部件"基础"自发光（悬停/选中高亮优先级更高）；hex 为 null 时清除 */
  setPartTint(partId, hex, intensity = DEFAULT_TINT_INTENSITY) {
    const p = this.parts.get(partId)
    if (!p) return
    p.tint = typeof hex === 'number' ? hex : null
    p.tintIntensity = typeof intensity === 'number' ? intensity : DEFAULT_TINT_INTENSITY
    this.applyColor(p)
  }

  resetPartsTint() {
    this.parts.forEach((p) => {
      p.tint = null
      this.applyColor(p)
    })
  }

  setPartVisible(partId, visible) {
    const p = this.parts.get(partId)
    if (p?.node) p.node.visible = !!visible
  }

  /** 全部部件位姿/可见性复位到 home（模式 exit 后的兜底） */
  resetPartsTransform() {
    this.parts.forEach((p) => {
      if (!p.node || !p.home) return
      p.node.position.copy(p.home.position)
      p.node.rotation.copy(p.home.rotation)
      p.node.scale.copy(p.home.scale)
      p.node.visible = p.home.visible
    })
    this.model?.updateMatrixWorld(true)
  }

  /** 相机聚焦部件锚点（verify.html focusComp 同款取距公式） */
  focusPart(partId) {
    const p = this.parts.get(partId)
    if (!p) return
    const dir = new THREE.Vector3(0.75, 0.42, 0.85).normalize()
    dir.multiplyScalar(Math.max(p.focusRadius * 3.2, 7))
    this.controls.target.copy(p.anchor)
    this.camera.position.copy(p.anchor).add(dir)
    this.controls.update()
  }

  /* ================= 加载 ================= */

  load() {
    const draco = new DRACOLoader()
    draco.setDecoderPath(DRACO_DECODER_PATH)
    const loader = new GLTFLoader()
    loader.setDRACOLoader(draco)

    Promise.all([
      new Promise((resolve, reject) =>
        loader.load(
          MODEL_URL,
          resolve,
          (ev) => {
            if (ev.total > 0) this.cb.onProgress?.(ev.loaded / ev.total)
          },
          reject,
        ),
      ),
      loadEquipmentMap(),
    ])
      .then(([gltf, map]) => {
        if (this.disposed) return
        this.equipmentMap = map
        this.model = gltf.scene
        // 关键：GLB 全部件共享同一材质实例，必须逐 mesh 克隆，否则高亮整机变色（verify.html 经验）
        this.model.traverse((o) => {
          if (o.isMesh) {
            o.material = Array.isArray(o.material)
              ? o.material.map((m) => this.cloneMaterial(m))
              : this.cloneMaterial(o.material)
          }
        })
        this.buildParts(map)
        this.scene.add(this.model)
        this.fitCamera()

        this.ready = true
        this._refitCountdown = 3
        if (this.pendingModeId) {
          const m = this.pendingModeId
          this.pendingModeId = null
          this.setMode(m)
        }
        this.cb.onReady?.({
          components: (map.components || []).map((c) => ({
            id: c.id,
            name_cn: c.name_cn,
            mesh: c.mesh,
            sensors: c.sensors,
            notes: c.notes,
          })),
          legendNote: map.thresholdLegend?.standards || '',
        })
      })
      .catch((err) => {
        if (this.disposed) return
        console.error('[Stage3D] 模型加载失败，回退静态视图', err)
        this.cb.onError?.(err)
      })
      .finally(() => draco.dispose())
  }

  /** 克隆材质并记录原始 emissive（高亮恢复基准） */
  cloneMaterial(src) {
    const m = src.clone()
    m.userData.__origEmissive = m.emissive ? m.emissive.getHex() : 0
    m.userData.__origIntensity = m.emissiveIntensity ?? 1
    return m
  }

  buildParts(map) {
    ;(map.components || []).forEach((c) => {
      const node = this.model.getObjectByName(c.mesh) || null
      const materials = []
      if (node) {
        node.traverse((o) => {
          if (o.isMesh) {
            ;(Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => materials.push(m))
          }
        })
      }
      // 卡片悬浮锚点：部件包围盒顶面中心相对锚点的抬升量（正视/侧视/背视时卡片都在部件轮廓上方）
      const anchor = new THREE.Vector3().fromArray(c.anchor || [0, 0, 0])
      let topOffY = 0
      if (node) {
        const bb = new THREE.Box3().setFromObject(node)
        if (!bb.isEmpty()) topOffY = bb.max.y + 0.45 - anchor.y
      }
      const part = {
        id: c.id,
        nameCn: c.name_cn,
        meshName: c.mesh,
        node,
        home: node
          ? {
              position: node.position.clone(),
              rotation: node.rotation.clone(),
              scale: node.scale.clone(),
              visible: node.visible,
            }
          : null,
        materials,
        anchor,
        topOffY,
        focusRadius: c.focusRadius || 2.5,
        comp: c,
        tint: null,
        tintIntensity: DEFAULT_TINT_INTENSITY,
      }
      this.parts.set(part.id, part)
      this.anchors.set(part.id, part.anchor)
      this.partIdByMeshName.set(c.mesh, part.id)
      // 场景就绪前已收到诊断结论（报告轮询先于模型加载完成）时，注册完即刻补刷高亮
      if (part.id === this.diagnosedId) this.applyColor(part)
    })
  }

  fitCamera() {
    // 显式刷新全链路世界矩阵：GLB 根节点带 ×12 缩放，若矩阵未更新会导致
    // 包围盒偏小 → 相机贴到模型脸上（首屏巨大的 bug）
    this.model.updateWorldMatrix(true, true)
    this.tmpBox.setFromObject(this.model)
    const size = this.tmpBox.getSize(new THREE.Vector3())
    const center = this.tmpBox.getCenter(new THREE.Vector3())

    // 按 FOV/长宽比精确取景：整机（含安全边距）始终满幅入画
    const fov = THREE.MathUtils.degToRad(this.camera.fov || 40)
    const aspect = Math.max(this.camera.aspect || 1, 0.1)
    const distH = size.y / 2 / Math.tan(fov / 2)
    const distW = size.x / 2 / (Math.tan(fov / 2) * aspect)
    const dist = Math.max(distH, distW) * 1.28 + size.z * 0.1
    this.fitDist = dist
    const dir = new THREE.Vector3(0.78, 0.52, 1).normalize()
    this.camera.position.copy(center).addScaledVector(dir, dist)
    this.controls.target.copy(center)
    this.controls.update()

    // 光晕盘铺在机器脚下（略大于占地，前向更多）
    const s = Math.max(size.x, size.z)
    this.glowDisc.position.set(center.x, this.tmpBox.min.y + 0.02, center.z)
    this.glowDisc.scale.set(s * 2.0, s * 2.4, 1)
  }

  buildGlowDisc() {
    const cv = document.createElement('canvas')
    cv.width = 256
    cv.height = 256
    const g = cv.getContext('2d')
    const grad = g.createRadialGradient(128, 128, 8, 128, 128, 128)
    grad.addColorStop(0, 'rgba(0, 199, 255, 0.5)')
    grad.addColorStop(0.45, 'rgba(0, 140, 220, 0.18)')
    grad.addColorStop(1, 'rgba(0, 120, 200, 0)')
    g.fillStyle = grad
    g.fillRect(0, 0, 256, 256)
    const tex = new THREE.CanvasTexture(cv)
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({
        map: tex,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        toneMapped: false,
      }),
    )
    mesh.rotation.x = -Math.PI / 2
    return mesh
  }

  /* ================= 交互（raycast 悬停/点击） ================= */

  bindEvents() {
    const el = this.renderer.domElement
    this.onPointerMove = (e) => {
      const r = el.getBoundingClientRect()
      this.ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1)
      this.ndcDirty = true
    }
    this.onPointerDown = (e) => {
      if (e.button === 0) this.downPos = { x: e.clientX, y: e.clientY }
    }
    this.onPointerUp = (e) => {
      if (!this.downPos || e.button !== 0) return
      const dx = e.clientX - this.downPos.x
      const dy = e.clientY - this.downPos.y
      this.downPos = null
      if (dx * dx + dy * dy > 36) return // 拖拽视角，不算点击
      this.pickAt(e.clientX, e.clientY, true)
    }
    this.onPointerLeave = () => {
      this.ndcDirty = false
      this.setHover(null)
    }
    this.onKeydown = (e) => {
      if (e.key === 'Escape') this.setSelect(null)
    }
    el.addEventListener('pointermove', this.onPointerMove)
    el.addEventListener('pointerdown', this.onPointerDown)
    el.addEventListener('pointerup', this.onPointerUp)
    el.addEventListener('pointerleave', this.onPointerLeave)
    window.addEventListener('keydown', this.onKeydown)
  }

  /** 从 NDC 拾取语义部件（命中后沿父链找到已登记 mesh 名的部件节点） */
  pickPartId() {
    if (!this.ready || !this.model) return null
    this.ray.setFromCamera(this.ndc, this.camera)
    const hits = this.ray.intersectObject(this.model, true)
    if (!hits.length) return null
    let node = hits[0].object
    while (node && !this.partIdByMeshName.has(node.name)) node = node.parent
    return node ? this.partIdByMeshName.get(node.name) || null : null
  }

  pickAt(clientX, clientY, isClick) {
    const r = this.renderer.domElement.getBoundingClientRect()
    this.ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1)
    const id = this.pickPartId()
    if (isClick) this.setSelect(id)
    else this.setHover(id)
    return id
  }

  pickHover() {
    this.setHover(this.pickPartId())
  }

  /* ================= 渲染循环 ================= */

  tick() {
    if (this.disposed) return
    this.raf = requestAnimationFrame(this.tick)
    const dt = this.clock.getDelta()

    // 就绪后前几帧再补一次取景：等容器尺寸/世界矩阵彻底稳定，保证首屏即正确构图
    if (this.ready && this._refitCountdown > 0) {
      this._refitCountdown -= 1
      if (this._refitCountdown === 0) this.fitCamera()
    }

    this.controls.update()
    if (this.ndcDirty && this.ready) {
      this.ndcDirty = false
      this.pickHover()
    }
    if (this.activeMode?.updateFrame) safeCall(() => this.activeMode.updateFrame(this.ctx, dt))
    this.frameFns.forEach((fn) => safeCall(fn, dt))
    this.tweens.forEach((t) => {
      if (safeCall(t.step, dt) === false) this.tweens.delete(t)
    })
    this.projectCards()
    this.renderer.render(this.scene, this.camera)
  }

  /** 标注卡片重投影：锚点 → 屏幕坐标（复用临时向量与条目对象，循环内零分配） */
  projectCards() {
    if (!this.ready) return
    const w = this.host.clientWidth
    const h = this.host.clientHeight
    let n = 0
    this.parts.forEach((part) => {
      this.tmpV.copy(part.anchor).project(this.camera)
      let item = this.projected[n]
      if (!item) {
        item = { id: part.id, x: 0, y: 0, visible: false }
        this.projected[n] = item
      }
      item.id = part.id
      item.x = (this.tmpV.x * 0.5 + 0.5) * w
      item.y = (-this.tmpV.y * 0.5 + 0.5) * h
      // 卡片悬浮点：部件包围盒顶面中心（正视/侧视/背视时都在部件轮廓上方）
      this.tmpV2.set(part.anchor.x, part.anchor.y + (part.topOffY || 0), part.anchor.z).project(this.camera)
      item.tx = (this.tmpV2.x * 0.5 + 0.5) * w
      item.ty = (-this.tmpV2.y * 0.5 + 0.5) * h
      item.visible = this.tmpV.z < 1 // 相机背后/超远裁剪的隐藏
      item.dist = this.camera.position.distanceTo(part.anchor)
      item.scale = THREE.MathUtils.clamp(this.fitDist / Math.max(item.dist, 0.001), 0.5, 1.3) // 近大远小
      item.fitDist = this.fitDist
      n += 1
    })
    this.cb.onProject?.(this.projected)
  }

  /* ================= 高亮层级：选中 > 悬停 > 诊断结论 > tint > 原色 ================= */

  applyColor(part) {
    const selected = part.id === this.selectId
    const hovered = part.id === this.hoverId
    const diagnosed = part.id === this.diagnosedId
    part.materials.forEach((m) => {
      if (!m.emissive) return
      if (selected) {
        m.emissive.setHex(HIGHLIGHT_HEX)
        m.emissiveIntensity = SELECT_INTENSITY
      } else if (hovered) {
        m.emissive.setHex(HIGHLIGHT_HEX)
        m.emissiveIntensity = HOVER_INTENSITY
      } else if (diagnosed) {
        // 诊断结论联动：最新报告指向的部件
        m.emissive.setHex(DIAGNOSED_HEX)
        m.emissiveIntensity = DIAGNOSED_INTENSITY
      } else if (part.tint != null) {
        m.emissive.setHex(part.tint)
        m.emissiveIntensity = part.tintIntensity ?? DEFAULT_TINT_INTENSITY
      } else {
        m.emissive.setHex(m.userData.__origEmissive ?? 0)
        m.emissiveIntensity = m.userData.__origIntensity ?? 1
      }
    })
  }

  /* ================= 其他 ================= */

  buildCtx() {
    const engine = this
    return {
      scene: engine.scene,
      camera: engine.camera,
      controls: engine.controls,
      renderer: engine.renderer,
      container: engine.host,
      get model() {
        return engine.model
      },
      parts: engine.parts,
      anchors: engine.anchors,
      get channels() {
        return engine.channels
      },
      channelsByName: engine.channelsByName,
      setPartTint: (id, hex, intensity) => engine.setPartTint(id, hex, intensity),
      resetPartsTint: () => engine.resetPartsTint(),
      setPartVisible: (id, visible) => engine.setPartVisible(id, visible),
      resetPartsTransform: () => engine.resetPartsTransform(),
      focusPart: (id) => engine.focusPart(id),
      addTween: (step) => engine.addTween(step),
      onFrame: (fn) => {
        engine.frameFns.add(fn)
        return () => engine.frameFns.delete(fn)
      },
    }
  }

  onResize() {
    const w = this.host.clientWidth || 1
    const h = this.host.clientHeight || 1
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h, false)
  }

  /** 彻底销毁：RAF/监听/观察器/模式/几何体/材质/贴图/渲染器，防轮询页切换内存泄漏 */
  dispose() {
    this.tweens.clear()
    if (this.disposed) return
    this.disposed = true
    this.ready = false
    cancelAnimationFrame(this.raf)
    this.ro?.disconnect()
    window.removeEventListener('keydown', this.onKeydown)
    const el = this.renderer.domElement
    el.removeEventListener('pointermove', this.onPointerMove)
    el.removeEventListener('pointerdown', this.onPointerDown)
    el.removeEventListener('pointerup', this.onPointerUp)
    el.removeEventListener('pointerleave', this.onPointerLeave)

    safeCall(() => this.activeMode?.dispose?.(this.ctx))
    this.frameFns.clear()

    if (this.model) {
      this.model.traverse((o) => {
        if (!o.isMesh) return
        o.geometry?.dispose()
        const mats = Array.isArray(o.material) ? o.material : [o.material]
        mats.forEach((m) => {
          Object.keys(m).forEach((k) => {
            const v = m[k]
            if (v && v.isTexture) v.dispose()
          })
          m.dispose()
        })
      })
    }

    this.scene.environment = null
    this.glowDisc?.material?.map?.dispose()
    this.glowDisc?.material?.dispose()
    this.glowDisc?.geometry?.dispose()
    this.pmrem?.dispose()
    this.controls?.dispose()
    this.renderer?.dispose()
    el.parentNode?.removeChild(el)
    this.cb = {}
  }
}
