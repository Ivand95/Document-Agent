import { useState } from 'react'
import { useNavigate } from 'react-router'
import { AutoAwesome, Visibility, VisibilityOff } from '@mui/icons-material'
import { CircularProgress, IconButton, InputAdornment, TextField } from '@mui/material'
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
                props.setUserInfo({ id: crypto.randomUUID(), name: 'Juan Niemen', email: 'juan.niemen@gmail.com', department: 'General' })
                navigate('/chat', { replace: true })
                resolve(true)
            }, 2000))
        } else {
            setLoading(false)
            setError('Correo electrónico o contraseña incorrectos')
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
                    <button type="submit" className="login-submit" disabled={loading}>
                        {loading ? <CircularProgress size={20} sx={{ color: 'var(--login-text)' }} /> : 'Iniciar sesión'}
                    </button>
                    {error && <p className="login-error">{error}</p>}
                </form>

                <p className="login-hint">
                    Asistente inteligente de documentación para <br /> <span className="login-hint-cooperativa">Cooperativa Barcelona</span>.
                </p>
            </div>
        </div>
    )
}
