// Members and roles of one system. Only owners reach the write API; viewers
// and editors land on a short explanation instead.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useLocation } from 'react-router-dom'

import {
  MembersApiError,
  ROLE_LABELS,
  addMember,
  fetchMembers,
  removeMember,
  updateMemberRole,
  type Member,
  type MemberList,
  type MemberRole,
} from '../api/members'
import { resolveAppRoute } from '../renderer/appRoute'

const ROLES: MemberRole[] = ['owner', 'editor', 'viewer']

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('zh-CN')
}

function errorText(failure: unknown): string {
  return failure instanceof Error ? failure.message : '操作失败，请重试'
}

export function AdminMembersPage() {
  const { appKey, isDemo } = resolveAppRoute(useLocation().pathname)
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<MemberRole>('viewer')
  const [notice, setNotice] = useState('')

  const members = useQuery({
    queryKey: ['members', appKey],
    queryFn: () => fetchMembers(appKey),
    retry: false,
    enabled: !isDemo,
  })

  function apply(next: MemberList) {
    queryClient.setQueryData(['members', appKey], next)
    setNotice('')
  }

  const add = useMutation({
    mutationFn: () => addMember(appKey, email.trim(), role),
    onSuccess: (next) => {
      apply(next)
      setEmail('')
    },
    onError: (failure) => setNotice(errorText(failure)),
  })

  const changeRole = useMutation({
    mutationFn: (payload: { userId: number; role: MemberRole }) =>
      updateMemberRole(appKey, payload.userId, payload.role),
    onSuccess: apply,
    onError: (failure) => setNotice(errorText(failure)),
  })

  const remove = useMutation({
    mutationFn: (userId: number) => removeMember(appKey, userId),
    onSuccess: apply,
    onError: (failure) => setNotice(errorText(failure)),
  })

  if (isDemo) {
    return (
      <div className="card admin-placeholder">
        <p>演示系统不支持管理后台。</p>
      </div>
    )
  }
  if (members.isLoading) return <p className="muted">正在加载成员…</p>
  if (members.isError) {
    const forbidden =
      members.error instanceof MembersApiError && members.error.status === 403
    return (
      <div className="card admin-placeholder">
        <p>{forbidden ? '没有权限管理成员。' : '无法加载成员列表。'}</p>
        <p className="muted">
          {forbidden
            ? '只有系统的所有者可以添加成员和调整角色。'
            : errorText(members.error)}
        </p>
      </div>
    )
  }

  const data = members.data
  if (!data) return null

  return (
    <section className="admin-page">
      <div className="admin-page-head">
        <div>
          <h1>成员与权限</h1>
          <p className="muted">
            所有者可以管理成员，编辑者可以修改模型和数据，查看者只能浏览。
          </p>
        </div>
      </div>

      <div className="card admin-panel">
        <h2>添加成员</h2>
        {data.unclaimed && (
          <p className="muted">
            这个系统在启用账号之前创建，目前所有登录账号都可以访问。添加第一位成员后，访问将仅限下列成员。
          </p>
        )}
        <form
          className="member-add"
          onSubmit={(event) => {
            event.preventDefault()
            add.mutate()
          }}
        >
          <input
            className="control"
            type="email"
            placeholder="成员邮箱"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
          <select
            className="control"
            value={role}
            onChange={(event) => setRole(event.target.value as MemberRole)}
          >
            {ROLES.map((item) => (
              <option key={item} value={item}>
                {ROLE_LABELS[item]}
              </option>
            ))}
          </select>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={add.isPending}
          >
            {add.isPending ? '添加中…' : '添加'}
          </button>
        </form>
        <p className="muted member-hint">
          对方需要先在本机注册账号，才能被添加到系统中。
        </p>
        {notice && <p className="field-error">{notice}</p>}
      </div>

      <div className="card admin-panel">
        <h2>成员</h2>
        {data.members.length === 0 ? (
          <p className="muted">还没有成员记录。</p>
        ) : (
          <table className="admin-table member-table">
            <thead>
              <tr>
                <th>账号</th>
                <th>角色</th>
                <th>加入时间</th>
                <th className="admin-num">操作</th>
              </tr>
            </thead>
            <tbody>
              {data.members.map((member: Member) => (
                <tr key={member.user_id}>
                  <td>
                    <span className="member-name">
                      {member.display_name || member.email}
                    </span>
                    {member.display_name && (
                      <span className="muted member-mail">{member.email}</span>
                    )}
                  </td>
                  <td>
                    <select
                      className="control member-select"
                      value={member.role}
                      disabled={changeRole.isPending}
                      onChange={(event) =>
                        changeRole.mutate({
                          userId: member.user_id,
                          role: event.target.value as MemberRole,
                        })
                      }
                    >
                      {ROLES.map((item) => (
                        <option key={item} value={item}>
                          {ROLE_LABELS[item]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="muted">{formatTime(member.added_at)}</td>
                  <td className="admin-num">
                    <button
                      type="button"
                      className="btn btn-mini btn-danger"
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(member.user_id)}
                    >
                      移除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
