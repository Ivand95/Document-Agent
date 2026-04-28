import axios from 'axios'

const http = axios.create({
    baseURL: import.meta.env.VITE_BACKEND_URL,
})

export const login = () => http.get('/login')

export const documentAgentWebSocket = (token: string) => {
    const ws = new WebSocket(`${import.meta.env.VITE_BACKEND_URL}/ws/chat?token=${token}`)
    return ws
}

export const audioAgentWebSocket = (token: string) => {
    const ws = new WebSocket(`${import.meta.env.VITE_BACKEND_URL}/ws/chat/audio?token=${token}`)
    return ws
}