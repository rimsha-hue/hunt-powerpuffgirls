import '@fontsource/bangers/400.css'
import '@fontsource/public-sans/400.css'
import '@fontsource/public-sans/600.css'
import '@fontsource/public-sans/800.css'
import '@fontsource/jetbrains-mono/500.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './index.css'

const root = document.getElementById('root')
if (!root) throw new Error('Root element #root is missing from index.html')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
