import { useState } from 'react'
import { useNavigate } from 'react-router'
import { AutoAwesome, Microsoft, Visibility, VisibilityOff } from '@mui/icons-material'
import { Button, CircularProgress, IconButton, InputAdornment, TextField } from '@mui/material'
import { login } from '../utils/axios'
import './style.css'

export const Login = (props: { userInfo: UserInfo, setUserInfo: (userInfo: UserInfo) => void }) => {
    const navigate = useNavigate()
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [showPassword, setShowPassword] = useState(false)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault()
        setLoading(true)
        if (email === 'juan.niemen@gmail.com' && password === '123456') {
            await new Promise((resolve) => setTimeout(() => {
                setLoading(false)
                props.setUserInfo({ access_token: '1234567890', name: 'Juan Niemen', email: 'juan.niemen@gmail.com', department: 'General' })
                navigate('/chat', { replace: true })
                resolve(true)
            }, 2000))
        } else {
            setLoading(false)
            setError('Correo electrónico o contraseña incorrectos')
        }
    }

    const handleLoginWithMicrosoft = async () => {
        try {
            const response = await login()
            if (response.status !== 200) return

            const width = 500
            const height = 700
            const left = window.screenX + (window.outerWidth - width) / 2
            const top = window.screenY + (window.outerHeight - height) / 2

            const popup = window.open(
                response.data.login_url,
                'microsoft-login',
                `width=${width},height=${height},left=${left},top=${top},popup=yes`
            )

            if (!popup) {
                setError('El navegador bloqueó la ventana emergente. Permite popups e intenta de nuevo.')
                return
            }

            setLoading(true)

            const onMessage = (event: MessageEvent) => {
                if (event.origin !== import.meta.env.VITE_BACKEND_URL) return
                if (event.data?.type !== 'ms-auth-callback') return

                window.removeEventListener('message', onMessage)
                setLoading(false)

                if (event.data.error) {
                    setError(event.data.error)
                    return
                }

                props.setUserInfo({
                    access_token: event.data.access_token,
                    name: event.data.name,
                    email: event.data.email,
                    department: event.data.department,
                })
                navigate('/chat', { replace: true })
            }

            window.addEventListener('message', onMessage)

            const pollTimer = setInterval(() => {
                if (popup.closed) {
                    clearInterval(pollTimer)
                    window.removeEventListener('message', onMessage)
                    setLoading(false)
                }
            }, 500)
        } catch (error) {
            console.error(error)
            setLoading(false)
            setError('Error al iniciar sesión con Microsoft')
        }
    }

    return (
        <div className="login-page">
            <div className="login-bg" aria-hidden />
            <div className="login-card">
                <div className="login-brand">
                    <div className="login-brand-icon">
                        <AutoAwesome sx={{ fontSize: 32 }} />
                    </div>
                    <h1 className="login-title">Document Agent</h1>
                    <p className="login-subtitle">Inicia sesión para continuar</p>
                </div>

                <form className="login-form" onSubmit={handleLogin}>
                    <TextField
                        type="email"
                        label="Correo electrónico"
                        placeholder="tu@email.com"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        required
                        fullWidth
                        variant="outlined"
                        autoComplete="email"
                        InputLabelProps={{ shrink: true }}
                        sx={{
                            '& .MuiInputLabel-root': { color: 'var(--login-text-muted)' },
                            '& .MuiInputLabel-root.Mui-focused': { color: 'var(--login-accent)' },
                            '& .MuiOutlinedInput-root': {
                                borderRadius: 'var(--login-radius)',
                                bgcolor: 'var(--login-input-bg)',
                                color: 'var(--login-text)',
                                '& fieldset': { borderColor: 'var(--login-border)' },
                                '&:hover fieldset': { borderColor: 'var(--login-accent)' },
                                '&.Mui-focused fieldset': {
                                    borderColor: 'var(--login-accent)',
                                    boxShadow: '0 0 0 2px rgba(95, 124, 58, 0.25)',
                                },
                            },
                        }}
                    />
                    <TextField
                        type={showPassword ? 'text' : 'password'}
                        label="Contraseña"
                        placeholder="••••••••"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                        fullWidth
                        variant="outlined"
                        autoComplete="current-password"
                        InputProps={{
                            endAdornment: (
                                <InputAdornment position="end">
                                    <IconButton
                                        aria-label={showPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'}
                                        onClick={() => setShowPassword(!showPassword)}
                                        edge="end"
                                        size="small"
                                        sx={{ color: 'var(--login-text-muted)' }}
                                    >
                                        {showPassword ? <VisibilityOff /> : <Visibility />}
                                    </IconButton>
                                </InputAdornment>
                            ),
                        }}
                        InputLabelProps={{ shrink: true }}
                        sx={{
                            '& .MuiInputLabel-root': { color: 'var(--login-text-muted)' },
                            '& .MuiInputLabel-root.Mui-focused': { color: 'var(--login-accent)' },
                            '& .MuiOutlinedInput-root': {
                                borderRadius: 'var(--login-radius)',
                                bgcolor: 'var(--login-input-bg)',
                                color: 'var(--login-text)',
                                '& fieldset': { borderColor: 'var(--login-border)' },
                                '&:hover fieldset': { borderColor: 'var(--login-accent)' },
                                '&.Mui-focused fieldset': {
                                    borderColor: 'var(--login-accent)',
                                    boxShadow: '0 0 0 2px rgba(95, 124, 58, 0.25)',
                                },
                            },
                        }}
                    />
                    <button type="submit" className="login-button" disabled={loading} style={{
                        color: '#fff',
                        background: 'linear-gradient(135deg, var(--login-accent) 0%, var(--login-accent-hover) 100%)'
                    }}>
                        {loading ? <CircularProgress size={20} sx={{ color: 'var(--login-text)' }} /> : 'Iniciar sesión'}
                    </button>
                    <Button
                        classes={{ root: 'login-button' }}
                        fullWidth
                        startIcon={<Microsoft />}
                        onClick={() => {
                            handleLoginWithMicrosoft()
                        }}
                        sx={{
                            backgroundColor: 'var(--login-text)',
                            color: 'var(--login-surface)',
                        }}
                    >
                        Iniciar sesión con Microsoft
                    </Button>
                    {error && <p className="login-error">{error}</p>}
                </form>

                <p className="login-hint">
                    Asistente inteligente de documentación para <br /> <span className="login-hint-cooperativa">Cooperativa Barcelona</span>.
                </p>
            </div>
        </div >
    )
}
