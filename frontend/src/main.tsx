import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import 'antd/dist/reset.css'
import './styles.css'
import './loading.css'
import './admin-ordering.css'
import App from './App'

const client = new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, retry: 1 } } })
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={client}><App /></QueryClientProvider></React.StrictMode>)
