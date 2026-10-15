import React from 'react'
import ReactDOM from 'react-dom/client'
import { App as AntApp, ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App.jsx'
import './theme.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      algorithm: theme.darkAlgorithm,
      token: {
        colorPrimary: '#00c7ff',
        colorBgBase: '#0a1a2f',
        colorBgContainer: '#0f2540',
        colorBgElevated: '#123152',
        colorBorder: '#1d3a5f',
        colorBorderSecondary: '#16304f',
        borderRadius: 6,
      },
    }}
  >
    {/* AntApp 为 message 等反馈类 API 提供主题上下文；component={false} 不渲染包裹 DOM，避免破坏 100% 高度布局 */}
    <AntApp component={false}>
      <App />
    </AntApp>
  </ConfigProvider>,
)
