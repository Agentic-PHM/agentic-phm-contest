import React, { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'

/**
 * 程序化动作库（AI 生成模型是单网格、无骨骼，动不了四肢——
 * 动作全部为整体位姿的连续形变：跳/倾/旋/摆，观感即"小机器人做动作"）。
 * fn(w, t01) 把 0→1 的进度写到包裹组 w 的位姿偏移上；dur 秒后由组件复位。
 */
export const AGENT_ACTIONS = {
  greet: { dur: 1.8, fn: (w, t) => { w.position.y = Math.sin(t * Math.PI * 2) * 0.05; w.rotation.x = -Math.sin(t * Math.PI) * 0.3; w.rotation.z = Math.sin(t * Math.PI * 3) * 0.08 } },
  bow: { dur: 1.2, fn: (w, t) => { w.rotation.x = -Math.sin(t * Math.PI) * 0.5 } },
  spin: { dur: 1.4, fn: (w, t) => { w.rotation.y = t * Math.PI * 2 } },
  hop: { dur: 0.9, fn: (w, t) => { w.position.y = Math.abs(Math.sin(t * Math.PI * 2)) * 0.1 } },
  nod: { dur: 1.2, fn: (w, t) => { w.rotation.x = Math.sin(t * Math.PI * 3) * 0.18 } },
  wiggle: { dur: 1.5, fn: (w, t) => { w.rotation.z = Math.sin(t * Math.PI * 4) * 0.14 } },
  happy: { dur: 1.5, fn: (w, t) => { w.position.y = Math.abs(Math.sin(t * Math.PI * 3)) * 0.07; w.rotation.z = Math.sin(t * Math.PI * 2) * 0.1 } },
  tilt: { dur: 1.3, fn: (w, t) => { w.rotation.z = Math.sin(t * Math.PI) * 0.22 } },
}

/**
 * 左下角/全局智能体 IP 形象（迷你 three.js 视窗）：
 * - 懒加载（父组件控制挂载时机）、透明背景、自动取景居中；
 * - 模型自带动画则播放，否则慢转台；action 传入动作名（如 greet#3）即播放程序化动作；
 * - url 变化即切换形象（旧模型完整 dispose）。
 */
export default function AgentAvatar3D({ url, action, spin = false, yaw = 0, zoom = 1, onStatus }) {
  const hostRef = useRef(null)
  const statusRef = useRef(onStatus)
  statusRef.current = onStatus
  const yawRef = useRef((yaw * Math.PI) / 180)

  const actionRef = useRef(null) // { name, fn, dur, t }：当前播放中的程序化动作（跨 effect 共享）

  useEffect(() => {
    const host = hostRef.current
    if (!host || !url) return undefined
    let disposed = false
    let raf = 0
    const modelRef = { current: null }
    const baseY = { current: 0 }
    const wrapper = new THREE.Group()

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setSize(host.clientWidth || 56, host.clientHeight || 56, false)
    renderer.domElement.style.width = '100%'
    renderer.domElement.style.height = '100%'
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100)
    const key = new THREE.DirectionalLight(0xffffff, 2.4)
    key.position.set(1.5, 2, 2)
    const rim = new THREE.DirectionalLight(0x66ccff, 1.3)
    rim.position.set(-2, 1, -1.5)
    const amb = new THREE.AmbientLight(0xffffff, 0.9)
    scene.add(key, rim, amb)
    scene.add(wrapper)

    const clock = new THREE.Clock()
    const frame = () => {
      raf = requestAnimationFrame(frame)
      const dt = clock.getDelta()
      if (modelRef.current) {
        if (spin) {
          modelRef.current.rotation.y += dt * 0.5 // 转台模式
        } else {
          // 桌面宠物待机：呼吸起伏 + 小幅张望，不转圈
          const tt = clock.elapsedTime
          modelRef.current.rotation.y = yawRef.current + Math.sin(tt * 0.7) * 0.14
          modelRef.current.position.y = baseY.current + Math.sin(tt * 1.4) * 0.012
        }
      }
      const act = actionRef.current
      if (act) {
        act.t += dt
        const t01 = Math.min(act.t / act.dur, 1)
        act.fn(wrapper, t01)
        if (t01 >= 1) {
          wrapper.position.set(0, 0, 0)
          wrapper.rotation.set(0, 0, 0)
          actionRef.current = null
        }
      }
      renderer.render(scene, camera)
    }
    frame()

    const draco = new DRACOLoader().setDecoderPath('/draco/')
    const loader = new GLTFLoader().setDRACOLoader(draco)
    loader.load(
      url,
      (gltf) => {
        try {
        if (disposed) {
          draco.dispose()
          return
        }
        const model = gltf.scene
        modelRef.current = model
        // AI 生成模型的包围盒易被游离杂件撑大 → 取景过远、形象偏小。
        // 稳健做法：逐 mesh 收集几何包围盒，以"最大体积 mesh"为锚，
        // 只并合中心距锚中心 2 倍对角线以内的邻近 mesh，剔除远处杂件。
        model.updateMatrixWorld(true)
        const meshBoxes = []
        model.traverse((o) => {
          if (!o.isMesh || !o.geometry) return
          o.geometry.computeBoundingBox()
          const g = o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld)
          if (g.isEmpty()) return
          meshBoxes.push(g)
        })
        let box = new THREE.Box3()
        if (meshBoxes.length) {
          const vol = (b) => {
            const sz = b.getSize(new THREE.Vector3())
            return Math.abs(sz.x * sz.y * sz.z) || 1e-9
          }
          const bc = new THREE.Vector3()
          let anchorBox = meshBoxes[0]
          for (const b of meshBoxes) if (vol(b) > vol(anchorBox)) anchorBox = b
          const anchorDiag = anchorBox.getSize(new THREE.Vector3()).length()
          anchorBox.getCenter(bc)
          box = anchorBox.clone()
          for (const b of meshBoxes) {
            if (b === anchorBox) continue
            const c = b.getCenter(new THREE.Vector3())
            if (c.distanceTo(bc) <= anchorDiag * 2) box.union(b)
          }
        }
        if (!box.isEmpty()) {
          const c = box.getCenter(new THREE.Vector3())
          model.position.sub(c)
          baseY.current = model.position.y
          const size = box.getSize(new THREE.Vector3())

          // 取景：模型高度撑满画布约 82%（宽度防溢出），zoom 逐形象微调。
          // 注意半角：竖直半视场角 = fov/2（35° 镜头即 17.5°），此前误用整个 fov
          // 导致距离少算一半、形象 2 倍超大只露脑袋。
          const halfTan = Math.tan(((camera.fov || 35) / 2) * Math.PI / 180)
          const distH = size.y / 2 / halfTan
          const distW = size.x / 2 / (halfTan * Math.max(camera.aspect || 1, 0.1))
          const d = Math.max(distH, distW) * 1.12
          window.__gaDbg = {
            meshes: meshBoxes.length,
            size: [size.x, size.y, size.z].map((v) => +v.toFixed(2)),
            d: +d.toFixed(2),
            aspect: +camera.aspect.toFixed(3),
          }
          camera.position.set(0, size.y * 0.05, d / Math.max(zoom, 0.2))
          camera.lookAt(0, 0, 0)
        }
        wrapper.add(model)
        // 注意：不自动播放 GLB 自带动画——AI 生成模型的动画轨常含缩放/位移，
        // 会把形象撑爆或移出取景（此前"忽大忽小/背对/闪跳"的根因）。
        // 形象的动感由程序化动作（AGENT_ACTIONS）与待机呼吸提供。
        draco.dispose()
        statusRef.current?.('ok')
        } catch (e) {
          window.__gaErr = String(e && e.stack || e)
          draco.dispose()
          statusRef.current?.('fail')
        }
      },
      undefined,
      (err) => {
        console.error('[AgentAvatar3D] GLB 加载失败:', url, err)
        if (!disposed) {
          draco.dispose()
          statusRef.current?.('fail')
        }
      },
    )

    const ro = new ResizeObserver(() => {
      const w = host.clientWidth || 56
      const h = host.clientHeight || 56
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    })
    ro.observe(host)

    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      ro.disconnect()
      draco.dispose()
      scene.traverse((o) => {
        if (o.isMesh) {
          o.geometry.dispose()
          ;(Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose())
        }
      })
      renderer.dispose()
      if (renderer.domElement.parentNode === host) host.removeChild(renderer.domElement)
    }
  }, [url])

  useEffect(() => {
    yawRef.current = (yaw * Math.PI) / 180
  }, [yaw])

  // 动作触发：action 形如 "greet#3"（名字#序号，重复同名也能重触发）
  useEffect(() => {
    if (!action) return
    const name = String(action).split('#')[0]
    const def = AGENT_ACTIONS[name]
    if (def) {
      // 包裹组当前位姿先归零，动作从标准姿态起播
      const host = hostRef.current
      void host
      actionRef.current = { name, fn: def.fn, dur: def.dur, t: 0 }
    }
  }, [action])

  return <div className="ga-canvas" ref={hostRef} />
}
