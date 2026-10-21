// Sign-in / sign-up gate. The first account created on an instance turns on
// authentication; before that the workbench stays open for the local user.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { fetchMe, login, register } from '../api/auth'
import { BrandLockup, CompanyFooter } from '../components/Brand'

type Mode = 'login' | 'register'

export function LoginPage() {
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState('')

  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, retry: false })

  useEffect(() => {
    if (me.data?.user) navigate('/', { replace: true })
  }, [me.data, navigate])

  const mutation = useMutation({
    mutationFn: () =>
      mode === 'login'
        ? login(email.trim(), password)
        : register(email.trim(), password, displayName.trim()),
    onSuccess: (state) => {
      queryClient.setQueryData(['me'], state)
      void queryClient.invalidateQueries({ queryKey: ['apps'] })
      navigate('/', { replace: true })
    },
    onError: (failure) => {
      setError(failure instanceof Error ? failure.message : '操作失败，请重试')
    },
  })

  function switchMode(next: Mode) {
    setMode(next)
    setError('')
  }

  return (
    <main className="gate">
      <div className="card gate-card login-card">
        <h1 className="gate-brand">
          <BrandLockup logoSize={34} />
        </h1>
        <h2>{mode === 'login' ? '登录' : '创建账号'}</h2>
        <p className="muted">
          {mode === 'login'
            ? '登录后继续使用你的系统。'
            : '第一个账号创建后，本机将需要登录才能进入工作台。'}
        </p>

        <form
          className="login-form"
          onSubmit={(event) => {
            event.preventDefault()
            setError('')
            mutation.mutate()
          }}
        >
          <div className="form-item">
            <label htmlFor="login-email">邮箱</label>
            <input
              id="login-email"
              className="control"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>

          {mode === 'register' && (
            <div className="form-item">
              <label htmlFor="login-name">显示名称（选填）</label>
              <input
                id="login-name"
                className="control"
                type="text"
                maxLength={32}
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </div>
          )}

          <div className="form-item">
            <label htmlFor="login-password">密码</label>
            <input
              id="login-password"
              className="control"
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={mode === 'register' ? 8 : undefined}
              required
            />
            {mode === 'register' && (
              <p className="muted login-hint">至少 8 位字符。</p>
            )}
          </div>

          {error && <p className="field-error">{error}</p>}

          <button
            type="submit"
            className="btn btn-primary login-submit"
            disabled={mutation.isPending}
          >
            {mutation.isPending
              ? '请稍候…'
              : mode === 'login'
                ? '登录'
                : '创建账号并登录'}
          </button>
        </form>

        <p className="muted login-switch">
          {mode === 'login' ? '还没有账号？' : '已经有账号？'}
          <button
            type="button"
            className="link-button"
            onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}
          >
            {mode === 'login' ? '创建账号' : '去登录'}
          </button>
        </p>
        <p className="muted login-back">
          <Link to="/">返回工作台</Link>
        </p>
      </div>
      <CompanyFooter />
    </main>
  )
}
