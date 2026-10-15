/**
 * 视图模式注册表 —— 3D 舞台四个视角模式的统一入口（F0 搭建）。
 *
 * ═══════════════════════ 模式接口契约（后续智能体必读） ═══════════════════════
 *
 * 每个模式是一个普通对象（放在本目录独立文件中，在下方 MODE_LIST 注册）：
 *
 *   {
 *     id: string,             // 与 Stage.jsx 页签 modeId 对应
 *     label: string,          // 页签文案
 *     enter(ctx),             // 切入该模式时调用一次
 *     update(ctx, channels),  // enter 后立即调用一次；此后每次 /api/channels 轮询
 *                             // 返回新数据时再调用（channels 为接口原始响应，可能为 null）
 *     exit(ctx),              // 切出时调用一次。exit 返回后框架会再做一次防御性复位
 *                             // （部件位姿/可见性 resetPartsTransform + 基础色 resetPartsTint），
 *                             // 因此 exit 里只需清理框架复位不了的东西（如自建 Mesh/覆盖层）
 *     dispose?(ctx),          // 可选：整个 Stage3D 组件卸载时调用
 *     updateFrame?(ctx, dt),  // 可选：每帧回调（dt 为秒）；也可用 ctx.onFrame(fn) 注册，
 *                             // 两种方式都会在模式切换时自动清理
 *   }
 *
 * ═══════════════════════ ctx 成员（Stage3D 构造，进入模式前已就绪） ═══════════════════════
 *
 *   scene / camera / controls / renderer   —— three.js 核心对象（相机 OrbitControls 已带阻尼）
 *   model                                  —— glTF 根节点（getter，加载后非空，已加入 scene）
 *   parts: Map<partId, PartHandle>         —— 9 个语义部件（equipment_map.components），
 *        PartHandle = {
 *          id, nameCn, meshName,
 *          node: Object3D|null,            —— 部件节点，改 position/rotation/scale 即拆图/动画
 *          home: {position,rotation,scale,visible}, —— 原始位姿（复位基准，勿改）
 *          materials: Material[],          —— 已逐 mesh 克隆的材质，可安全改 emissive；
 *                                             不要改共享贴图，不要绕过克隆直接改原材质
 *          anchor: Vector3,                —— 标注卡片锚点（glTF 世界坐标，含根 ×12 缩放）；
 *                                             模式若移动了部件，请同步 part.anchor.copy(新世界坐标)
 *                                             让卡片跟随
 *          focusRadius, comp,              —— equipment_map 原始条目（comp.sensors 绑定通道等）
 *          tint, tintIntensity,            —— 当前基础色（框架管理，模式经 setPartTint 修改）
 *        }
 *   anchors: Map<partId, Vector3>          —— 各部件锚点快捷表（与 parts.get(id).anchor 同引用）
 *   setPartTint(partId, hexOrNull, intensity) —— 设部件"基础"自发光色（null=清除）；
 *                                               悬停/选中高亮（青色）由框架处理，优先级更高
 *   resetPartsTint()                       —— 清除全部基础色
 *   setPartVisible(partId, visible)        —— 部件显隐
 *   resetPartsTransform()                  —— 全部部件位姿/可见性复位到 home
 *   focusPart(partId)                      —— 相机聚焦部件锚点（按 focusRadius 定距）
 *   channels                               —— 最新 /api/channels 响应（getter，可能为 null）
 *   channelsByName: Map<通道名, 通道对象>   —— 同上数据的按名索引（Map 引用稳定，内容随轮询刷新）
 *   onFrame(fn) -> 取消注册函数             —— 注册每帧回调（dt 秒），切模式/卸载自动清理
 *   container                              —— 舞台 DOM 容器（挂自建 HTML/子视图用，注意自行清理）
 *
 * 注意事项：
 * - 禁止在模式里新建第二套渲染循环/第二个 renderer；动画一律走 updateFrame 或 onFrame。
 * - 每帧回调里避免新建对象（Vector3/材质等复用或缓存到闭包）。
 * - 业务数值一律来自 channels/equipment_map，禁止硬编码。
 */
import composeMode from './composeMode.js'
import explodeMode from './explodeMode.js'
import thermalMode from './thermalMode.js'
import vibrationMode from './vibrationMode.js'

/** 页签顺序即舞台右上角展示顺序 */
export const MODE_LIST = [composeMode, explodeMode, thermalMode, vibrationMode]

export const MODE_MAP = Object.fromEntries(MODE_LIST.map((m) => [m.id, m]))
