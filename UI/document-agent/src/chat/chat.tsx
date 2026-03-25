import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import './style.css'
import { ArrowDownward, AutoAwesome, Logout, Send } from '@mui/icons-material'
import { Avatar, Button, CircularProgress, IconButton } from '@mui/material'
import { useNavigate } from 'react-router'
import { chatWebSocket } from '../utils/axios'
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

type MessageRole = 'user' | 'assistant'

interface Message {
    id: string
    role: MessageRole
    content: string
    timestamp: Date
}

interface UserInfo {
    access_token: string
    name: string
    email: string
    department: string
}

export const Chat = (props: { userInfo: UserInfo, setUserInfo: (userInfo: UserInfo) => void }) => {
    const [messages, setMessages] = useState<Message[]>([])
    const [input, setInput] = useState('')
    const [showScrollDown, setShowScrollDown] = useState(false)
    const messagesContainerRef = useRef<HTMLDivElement>(null)
    const messagesEndRef = useRef<HTMLDivElement>(null)
    const textareaRef = useRef<HTMLTextAreaElement>(null)
    const [thinking, setThinking] = useState(false)
    const [openUserMenu, setOpenUserMenu] = useState(false)
    const navigate = useNavigate()
    const [wsConnected, setWsConnected] = useState(false)
    const wsRef = useRef<WebSocket>(null)

    useEffect(() => {
        if (!wsRef.current) {
            wsRef.current = chatWebSocket(props.userInfo.access_token)
            wsRef.current.onopen = () => {
                setWsConnected(true)
            }
            wsRef.current.onclose = () => {
                setWsConnected(false)
            }
            wsRef.current.onerror = (error: Event) => {
                console.error('WebSocket error', error)
                setWsConnected(false)
            }
        }
    }, [props.userInfo.access_token, wsRef.current])

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
        setShowScrollDown(false)
    }

    const handleMessagesScroll = () => {
        const el = messagesContainerRef.current
        if (!el) return

        const threshold = 8 // px de tolerancia
        const atBottom =
            el.scrollHeight - el.scrollTop - el.clientHeight <= threshold

        setShowScrollDown(!atBottom)
    }

    useEffect(() => {
        if (wsConnected && wsRef.current) {
            wsRef.current.onmessage = (event: MessageEvent) => {
                const data = JSON.parse(event.data)
                if (data.type === 'answer' || data.type === 'error') {
                    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: 'assistant', content: data.content, timestamp: new Date() }])
                    setThinking(false)
                }
            }
        }
    }, [wsConnected])

    useEffect(() => {
        scrollToBottom()
    }, [messages])

    useLayoutEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto'
            textareaRef.current.style.height = `${Math.max(textareaRef.current.scrollHeight, 16)}px`
        }
    }, [input])

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()
        setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: 'user', content: input, timestamp: new Date() }])
        if (wsRef.current) {
            wsRef.current.send(input)
            setInput('')
            textareaRef.current?.focus()
            setThinking(true)
        }
    }

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey && !thinking) {
            e.preventDefault()
            handleSubmit(e)
        }
    }

    const handleLogout = () => {
        navigate('/', { replace: true })
        props.setUserInfo({ access_token: '', name: '', email: '', department: '' })
    }

    const handleOpenUserMenu = () => {
        setOpenUserMenu(!openUserMenu);
    }

    return (
        <div className="chat-dashboard">
            <header className="chat-header">
                <div className="chat-header-inner">
                    <div className="chat-header-icon">
                        <AutoAwesome sx={{ fontSize: 28 }} />
                    </div>
                    <div>
                        <h1 className="chat-title">Document Agent</h1>
                        <p className="chat-subtitle">Asistente inteligente de documentación</p>
                    </div>
                </div>
                <div className="chat-header-user">
                    <div className="chat-header-user-avatar">
                        <IconButton size='small' aria-label='Abrir menú de usuario' onClick={handleOpenUserMenu}>
                            <Avatar sx={{ fontSize: 28, color: 'var(--chat-text)' }} />
                        </IconButton>
                    </div>
                    {openUserMenu && (
                        <div className="chat-header-user-info">
                            <div className="chat-header-user-info-header">
                                <Avatar
                                    sx={{
                                        width: 40,
                                        height: 40,
                                        fontSize: '1rem',
                                        fontWeight: 600,
                                        bgcolor: 'var(--chat-accent)',
                                        color: 'var(--chat-text)',
                                    }}
                                >
                                    {props.userInfo?.name?.charAt(0).toUpperCase()}
                                </Avatar>
                                <div className="chat-header-user-info-content">
                                    <p className="chat-header-user-info-name">{props.userInfo?.name || ''}</p>
                                    <p className="chat-header-user-info-email">{props.userInfo?.email || ''}</p>
                                    <p className="chat-header-user-info-department">{props.userInfo?.department || ''}</p>
                                </div>
                            </div>
                            <div className="chat-header-user-info-footer">
                                <Button
                                    onClick={handleLogout}
                                    size="small"
                                    aria-label="Cerrar sesión"
                                    variant="text"
                                    fullWidth
                                    sx={{
                                        justifyContent: 'flex-start',
                                        gap: 1,
                                        color: 'var(--chat-text-muted)',
                                        '&:hover': {
                                            bgcolor: 'rgba(255,255,255,0.06)',
                                            color: 'var(--chat-text)',
                                        },
                                    }}
                                    startIcon={<Logout sx={{ fontSize: 18 }} />}
                                >
                                    Cerrar sesión
                                </Button>
                            </div>
                        </div>
                    )}
                </div>
            </header>

            <main className="chat-main">
                <div
                    ref={messagesContainerRef}
                    className="chat-messages"
                    onScroll={handleMessagesScroll}
                >
                    {messages.length === 0 ? (
                        <div className="chat-welcome">
                            <div className="chat-welcome-icon">
                                <AutoAwesome sx={{ fontSize: 28 }} />
                            </div>
                            <h2>¿En qué puedo ayudarte?</h2>
                            <p>Escribe tu pregunta o indica qué documento quieres analizar.</p>
                            <div className="chat-welcome-suggestions">
                                <button
                                    type="button"
                                    className="suggestion-chip"
                                    onClick={() => setInput('Plan estratégico de la cooperativa')}
                                >
                                    Plan estratégico de la cooperativa
                                </button>
                                <button
                                    type="button"
                                    className="suggestion-chip"
                                    onClick={() => setInput('Funciones del equipo de trabajo')}
                                >
                                    Funciones del equipo de trabajo
                                </button>
                                <button
                                    type="button"
                                    className="suggestion-chip"
                                    onClick={() => setInput('Valores de la cooperativa')}
                                >
                                    Valores de la cooperativa
                                </button>
                            </div>
                        </div>
                    ) : (
                        <>
                            {messages.map((msg) => (
                                <div
                                    key={msg.id}
                                    className={`chat-message chat-message--${msg.role}`}
                                    role="article"
                                    aria-label={msg.role === 'user' ? 'Tu mensaje' : 'Respuesta del agente'}
                                >
                                    {msg.role === 'assistant' && (
                                        <div className="chat-message-avatar" aria-hidden>
                                            <AutoAwesome sx={{ fontSize: 28 }} />
                                        </div>
                                    )}
                                    <div className="chat-message-bubble">
                                        <div className="chat-message-content">
                                            <Markdown remarkPlugins={[remarkGfm]}>
                                                {msg.content}
                                            </Markdown>
                                        </div>
                                        <time
                                            className="chat-message-time"
                                            dateTime={msg.timestamp.toISOString()}
                                        >
                                            {msg.timestamp.toLocaleTimeString('es-ES', {
                                                hour: '2-digit',
                                                minute: '2-digit',
                                            })}
                                        </time>
                                    </div>
                                </div>
                            ))}

                            {thinking && (
                                <div
                                    className="chat-message chat-message--assistant"
                                    role="status"
                                    aria-label="El agente está pensando"
                                >
                                    <div className="chat-message-avatar" aria-hidden>
                                        <AutoAwesome sx={{ fontSize: 28 }} />
                                    </div>
                                    <div className="chat-message-bubble chat-message-bubble--thinking">
                                        <CircularProgress size={18} sx={{ color: 'var(--chat-text)' }} />
                                        <span className="chat-message-thinking-text">Analizando…</span>
                                    </div>
                                </div>
                            )}

                            <div ref={messagesEndRef} className="chat-messages-anchor" aria-hidden />
                        </>
                    )}
                </div>

                {showScrollDown && messages.length > 0 && (
                    <IconButton
                        className="chat-arrow-down"
                        color="inherit"
                        onClick={scrollToBottom}
                        size="small"
                    >
                        <ArrowDownward fontSize="small" />
                    </IconButton>
                )}

                <form className="chat-input-wrap" onSubmit={handleSubmit}>
                    <div className="chat-input-inner">
                        <textarea
                            ref={textareaRef}
                            className="chat-input"
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="Escribe tu mensaje… (Enter para enviar, Shift+Enter para nueva línea)"
                            aria-label="Mensaje"
                            style={{ minHeight: '16px', resize: 'none' }}
                        />
                        <button
                            type="submit"
                            className="chat-send"
                            disabled={!input.trim() || thinking}
                            aria-label="Enviar mensaje"
                        >
                            {thinking ? <CircularProgress size={20} sx={{ color: 'var(--chat-text)' }} /> : <Send sx={{ fontSize: 20 }} />}
                        </button>
                    </div>
                    <p className="chat-input-hint">
                        Document Agent puede cometer errores. Verifica la información importante.
                    </p>
                </form>
            </main>
        </div>
    )
}
