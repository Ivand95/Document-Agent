import axios from 'axios'

const http = axios.create({
    baseURL: import.meta.env.VITE_BACKEND_URL,
})

export const login = () => http.get('/login')

export const chatWebSocket = (token: string) => {
    const ws = new WebSocket(`${import.meta.env.VITE_BACKEND_URL}/ws/chat?token=${token}`)
    return ws
}