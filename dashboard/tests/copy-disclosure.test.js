import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

import {
  AGENT_TOPOLOGY_NOTE,
  DEPLOYMENT_NOTE,
  SIMULATION_DISCLOSURE,
  SIMULATION_TAG,
} from '../src/dataProvenance.js'

/**
 * P2 口径回归阀（两条）：
 * 1) 仿真数据标识必须常驻页面——每页顶栏与登录页都要渲染，且文案单一来源；
 * 2) 不得把顺序执行的诊断流程宣传为独立的并行多智能体协同。
 *
 * 扫描范围只到 src/：docs/ 与 evidence/ 是历史记录，允许保留当时的旧设计表述。
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? walk(full) : [full]
  })
}

const SOURCES = walk(SRC).filter((f) => /\.(js|jsx)$/.test(f))

test('P2-1：仿真数据标识文案明确说明是程序合成、不是现场测量值', () => {
  assert.equal(SIMULATION_TAG, '仿真数据')
  assert.match(SIMULATION_DISCLOSURE, /仿真/)
  assert.match(SIMULATION_DISCLOSURE, /合成/)
  assert.match(SIMULATION_DISCLOSURE, /不是现场测量值/)
})

test('P2-1：每页顶栏与登录页都渲染仿真数据标识（常驻，不依赖具体页面）', () => {
  const app = readFileSync(join(SRC, 'App.jsx'), 'utf8')
  const login = readFileSync(join(SRC, 'pages', 'Login.jsx'), 'utf8')
  // 顶栏在 App 内、Content 之外，切页不卸载，因此标识对每个页面都常驻
  assert.match(app, /SIMULATION_TAG/, 'App.jsx 顶栏必须渲染仿真数据标识')
  assert.match(login, /SIMULATION_TAG/, 'Login.jsx 必须渲染仿真数据标识')
})

test('P2-2：界面源码不含"独立多智能体协同"类表述', () => {
  const BANNED = ['工单闭环智能体', '闭环智能体', '多智能体', '智能体协同', '智能体集群']
  const hits = []
  for (const file of SOURCES) {
    const text = readFileSync(file, 'utf8')
    for (const phrase of BANNED) {
      if (text.includes(phrase)) hits.push(`${relative(SRC, file)} 含「${phrase}」`)
    }
  }
  assert.deepEqual(hits, [], `界面文案不得宣传未经验证的多智能体协同：\n${hits.join('\n')}`)
})

test('P2-2：工作流页显式声明三段智能体阶段由同一 AGH 会话顺序执行', () => {
  const workflow = readFileSync(join(SRC, 'pages', 'Workflow.jsx'), 'utf8')
  assert.match(workflow, /AGENT_TOPOLOGY_NOTE/, 'Workflow.jsx 必须渲染单会话顺序执行的口径')
  assert.match(AGENT_TOPOLOGY_NOTE, /同一个 AGH 会话/)
  assert.match(AGENT_TOPOLOGY_NOTE, /顺序执行/)
  // 阶段执行者不再逐段标注为独立智能体
  assert.doesNotMatch(workflow, /executor: '诊断智能体'/)
})

test('工单文案纪律：关闭态一律写「已关闭」，不得回落成「已完成」', () => {
  const hits = []
  for (const file of SOURCES) {
    const text = readFileSync(file, 'utf8')
    // 只查工单状态三元表达式；工作流阶段自身的 done 文案（STAGES/STATUS_META）不在此列
    if (/'open'\s*\?\s*'待处理'\s*:\s*'已完成'/.test(text)) hits.push(relative(SRC, file))
  }
  assert.deepEqual(hits, [], `工单关闭态文案必须为「已关闭」：${hits.join('、')}`)
})

test('P1-5：不得把仿真恢复验证写成维修完成或处置闭环完成', () => {
  const BANNED = ['已关闭（复测通过）', '本次处置闭环完成', '已修好', '维修完成']
  const hits = []
  for (const file of SOURCES) {
    const text = readFileSync(file, 'utf8')
    for (const phrase of BANNED) {
      if (text.includes(phrase)) hits.push(`${relative(SRC, file)} 含「${phrase}」`)
    }
  }
  assert.deepEqual(hits, [],
    `解除仿真注入不等于设备已修好，界面文案不得这样写：\n${hits.join('\n')}`)
})

test('P1-6：界面明确声明这是本机演示，不是线上服务', () => {
  assert.match(DEPLOYMENT_NOTE, /本机离线演示/)
  assert.match(DEPLOYMENT_NOTE, /回环/)
  assert.match(DEPLOYMENT_NOTE, /不可直接对外暴露/)
  const login = readFileSync(join(SRC, 'pages', 'Login.jsx'), 'utf8')
  assert.match(login, /DEPLOYMENT_NOTE/, '登录页必须展示部署定位')
})
