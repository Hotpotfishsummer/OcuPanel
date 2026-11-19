import { useCallback, useEffect, useRef, useState } from 'react'
import DebugDrawer from './components/DebugDrawer.jsx'
import LoginPage from './components/LoginPage.jsx'
import Sidebar from './components/Sidebar.jsx'
import WelcomeView from './components/WelcomeView.jsx'
import ChatView from './components/ChatView.jsx'
import ReportView from './components/ReportView.jsx'
import SettingsView from './components/SettingsView.jsx'
import {
  AuthError,
  changeConversationMode,
  createConversation,
  deleteConversation,
  demoLogin,
  downloadReportPdf,
  fetchMe,
  generateReportContent,
  getConversation,
  getReportImage,
  getToken,
  listConversations,
  logout as apiLogout,
  sendMessage,
  setToken
} from './api.js'

export function describeError(err) {
  if (err instanceof TypeError) {
    return '无法连接后端服务。请确认后端已启动（backend 目录下运行 python main.py），然后刷新页面重试。'
  }
  return err?.message || String(err)
}

let seq = 0
const nextId = () => `local-${++seq}`

export default function App() {
  const [booting, setBooting] = useState(true)
  const [user, setUser] = useState(null)
  const [llm, setLlm] = useState(null)
  // 演示自动登录是否生效（由后端决定，前端只读）。为 true 时不渲染登录/注册页，
  // 也不显示任何登出入口 —— 清了登录态也会被立刻自动重登，露出来只会让人困惑。
  const [demoMode, setDemoMode] = useState(false)

  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [chatMode, setChatMode] = useState('general')

  // view: 'welcome' | 'chat' | 'report' | 'settings'
  const [view, setView] = useState('welcome')
  const [busy, setBusy] = useState(false)
  const [imageUrls, setImageUrls] = useState({})

  const imageUrlsRef = useRef({})
  imageUrlsRef.current = imageUrls

  /** 只清本地会话状态，不做任何跳转决策。 */
  const clearSession = () => {
    setToken('')
    setUser(null)
    setLlm(null)
    setConversations([])
    setMessages([])
    setActiveId(null)
    setChatMode('general')
    setView('welcome')
  }

  /**
   * 演示模式下静默重登。
   * token 过期（401）在演示模式里不该弹登录页：那会让"直接进主界面"破功，
   * 而且演示账号的密码本来就不是给使用者记的。重登失败才回落到登录页。
   */
  const reauthDemo = useCallback(async () => {
    try {
      const data = await demoLogin()
      setToken(data.token)
      setUser(data.user)
      setDemoMode(Boolean(data.demo))
      try {
        const me = await fetchMe()
        setLlm(me.llm)
      } catch {
        /* 已登录，llm 拿不到不影响主流程 */
      }
      return true
    } catch {
      // 后端把演示模式关了，或者服务不可达 —— 只能回落到登录页
      setDemoMode(false)
      clearSession()
      return false
    }
  }, [])

  const guard = useCallback(
    (err) => {
      if (!(err instanceof AuthError)) return false
      if (demoMode) {
        reauthDemo()
      } else {
        clearSession()
      }
      return true
    },
    [demoMode, reauthDemo]
  )

  // 启动：有 token 就恢复登录态；没有（或失效）就走演示自动登录，失败才落到登录页
  useEffect(() => {
    let cancelled = false

    const boot = async () => {
      const token = getToken()
      if (token) {
        try {
          const data = await fetchMe()
          if (cancelled) return
          setUser(data.user)
          setLlm(data.llm)
          setDemoMode(Boolean(data.demo))
          return
        } catch {
          setToken('')
        }
      }

      try {
        const data = await demoLogin()
        if (cancelled) return
        setToken(data.token)
        setUser(data.user)
        setDemoMode(Boolean(data.demo))
        try {
          const me = await fetchMe()
          if (!cancelled) setLlm(me.llm)
        } catch {
          /* 已登录，llm 拿不到不影响主流程 */
        }
      } catch {
        // 后端没开演示模式（404）或不可达：保持 demoMode=false，正常显示登录页
        if (!cancelled) setDemoMode(false)
      }
    }

    boot().finally(() => {
      if (!cancelled) setBooting(false)
    })

    return () => {
      cancelled = true
    }
  }, [])

  const refreshConversations = useCallback(async () => {
    try {
      const data = await listConversations()
      setConversations(data.items || [])
    } catch (err) {
      guard(err)
    }
  }, [guard])

  useEffect(() => {
    if (user) refreshConversations()
  }, [user, refreshConversations])

  // 卸载时回收所有 blob url（分割图）
  useEffect(
    () => () => {
      Object.values(imageUrlsRef.current).forEach((u) => URL.revokeObjectURL(u))
    },
    []
  )

  const onAuthed = async (data) => {
    setToken(data.token)
    setUser(data.user)
    setDemoMode(Boolean(data.demo))
    setView('welcome')
    try {
      const me = await fetchMe()
      setLlm(me.llm)
    } catch {
      /* 已登录，llm 拿不到不影响主流程 */
    }
    refreshConversations()
  }

  /** 打开某个会话：从数据库读回全部消息与报告图。 */
  const openConversation = async (id) => {
    if (busy) return
    setBusy(true)
    try {
      const data = await getConversation(id)
      const msgs = data.messages || []

      // 报告消息里的分割图需要单独取回成 blob
      const nextImages = { ...imageUrls }
      for (const m of msgs) {
        const name = m.meta?.img_name
        if (name && !nextImages[name]) {
          try {
            const blob = await getReportImage(name)
            nextImages[name] = URL.createObjectURL(blob)
          } catch {
            /* 图没了不影响文字 */
          }
        }
      }
      setImageUrls(nextImages)
      setMessages(msgs.map((m) => ({ ...m, id: m.id || nextId() })))
      setActiveId(id)
      setChatMode(data.conversation?.mode || 'general')
      setView('chat')
    } catch (err) {
      if (!guard(err)) alert(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const newChat = () => {
    if (busy) return
    setActiveId(null)
    setChatMode('general')
    setMessages([])
    // 直接进对话界面，不要回欢迎页。
    // 欢迎页只有引导卡片、没有输入框，点「新建对话」却停在那一屏，
    // 用户既看不到跳转也无法立刻打字，会以为按钮没反应。
    setView('chat')
  }

  /** 确保有会话 id：首次发消息时懒创建，避免点一次"新建"就留一条空记录。 */
  const ensureConversation = async (mode = chatMode) => {
    if (activeId) return activeId
    const conv = await createConversation('新对话', mode)
    setActiveId(conv.id)
    setConversations((prev) => [conv, ...prev])
    return conv.id
  }

  const handleModeChange = async (mode) => {
    if (busy || mode === chatMode) return
    if (!activeId) {
      setChatMode(mode)
      return
    }
    setBusy(true)
    try {
      await changeConversationMode(activeId, mode)
      setChatMode(mode)
      setConversations((prev) => prev.map((c) => c.id === activeId ? { ...c, mode } : c))
    } catch (err) {
      if (!guard(err)) alert(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const openAnalysis = () => {
    if (!busy) setView('report')
  }

  const ask = async (text, convId) => {
    const pendingId = nextId()
    setMessages((prev) => [...prev, { id: pendingId, role: 'assistant', content: '', pending: true }])
    try {
      const data = await sendMessage(convId, text)
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId ? { ...m, pending: false, content: data.reply, meta: {} } : m
        )
      )
      refreshConversations()
    } catch (err) {
      if (guard(err)) return
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? { ...m, pending: false, content: `⚠️ ${describeError(err)}`, meta: {} }
            : m
        )
      )
    }
  }

  const handleSend = async (text) => {
    if (busy) return
    setBusy(true)
    setMessages((prev) => [...prev, { id: nextId(), role: 'user', content: text }])
    try {
      const convId = await ensureConversation()
      setView('chat')
      await ask(text, convId)
    } catch (err) {
      if (!guard(err)) alert(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const handleGenerate = async (file, note) => {
    if (busy) return
    setBusy(true)

    // 占位消息必须在跳转前插入。
    // 原来这里是 setView('chat') 之后直接 await 报告接口，而那几十秒里
    // messages 仍然是空的 —— 对话区渲染的是"有什么可以帮你"的引导空态，
    // 和新开一个对话长得一模一样，用户没有任何依据判断报告在生成还是没开始。
    // 这里先放一条带 pending_report 标记的占位消息，生成结束后原地替换。
    const pendingId = nextId()
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: 'user', content: `眼科分析：${file.name}\n${note || ''}` },
      {
        id: pendingId,
        role: 'assistant',
        content: '',
        pending: true,
        meta: { kind: 'report', pending_report: true, started_at: Date.now() }
      }
    ])
    setView('chat')

    try {
      const convId = await ensureConversation('ophthalmic')
      await changeConversationMode(convId, 'ophthalmic')
      setChatMode('ophthalmic')
      const info = await generateReportContent(file, note, convId)

      let imgUrl = null
      if (info.img_name) {
        try {
          const blob = await getReportImage(info.img_name)
          imgUrl = URL.createObjectURL(blob)
          setImageUrls((prev) => ({ ...prev, [info.img_name]: imgUrl }))
        } catch {
          /* 取图失败不影响正文 */
        }
      }

      // 沿用 pendingId 原地替换，不要 push 新消息：
      // 换 id 会让 React 卸载再挂载气泡，滚动位置会跟着跳。
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? {
                ...m,
                pending: false,
                content: info.summary,
                meta: {
                  kind: 'report',
                  img_name: info.img_name,
                  evidence: info.evidence || [],
                  verification: info.verification,
                  rejected: info.rejected,
                  cup_to_disc_ratio: info.cup_to_disc_ratio,
                  // 原始上传件只留在本条消息里，用来在对话中补「下载 PDF」。
                  // 不进数据库：刷新/重开会话后 meta 从 SQLite 读回，这两个字段没有，
                  // 按钮自然不渲染（PDF 接口需要原图，没法凭空重建）。
                  source_file: file,
                  source_note: note || ''
                }
              }
            : m
        )
      )
      refreshConversations()

      // 拒答时后端不产出 PDF，这里也不该让"下载"看起来可用
      if (info.rejected) {
        alert('本次报告未通过引用校验，已拒答，不提供 PDF 下载。详情见对话中的校验结果。')
      }
      return !info.rejected
    } catch (err) {
      // 失败同样要消费掉占位消息，否则它会一直转圈，看起来像卡死
      const text = err instanceof AuthError ? '登录已过期，请重新登录。' : describeError(err)
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId ? { ...m, pending: false, content: `⚠️ ${text}`, meta: {} } : m
        )
      )
      if (!guard(err)) alert(describeError(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  const handleDownloadPdf = async (file, note) => {
    setBusy(true)
    try {
      const blob = await downloadReportPdf(file, note, activeId || '')
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'diagnostic_report.pdf'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      if (!guard(err)) alert(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const handleDeleteConversation = async (id) => {
    if (!confirm('删除这个对话？记录将从数据库中移除，不可恢复。')) return
    try {
      await deleteConversation(id)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (id === activeId) {
        setActiveId(null)
        setChatMode('general')
        setMessages([])
        // 同理：删完直接给一个空白可输入的对话页，别弹回没有输入框的欢迎页
        setView('chat')
      }
    } catch (err) {
      if (!guard(err)) alert(describeError(err))
    }
  }

  const handleLogout = async () => {
    // 演示模式下不存在"退出"：清了登录态下一步就被自动重登，按钮已经隐藏，
    // 这里再兜一层，避免误触后短暂闪一下登录页。
    if (demoMode) return
    if (!confirm('退出登录？对话与报告已保存在服务端，重新登录后仍在。')) return
    try {
      await apiLogout()
    } catch {
      /* token 可能已过期，忽略——本地照样要清掉 */
    }
    clearSession()
  }

  if (booting) {
    return (
      <div className="app-boot">
        <div className="spinner" />
        正在加载…
      </div>
    )
  }

  if (!user) return <LoginPage onAuthed={onAuthed} />

  const activeConv = conversations.find((c) => c.id === activeId)
  const notConfigured = !llm?.has_key

  return (
    <div className="app-shell">
      <Sidebar
        user={user}
        conversations={conversations}
        activeId={activeId}
        view={view}
        onNewChat={newChat}
        onOpenReport={openAnalysis}
        onSelectConversation={openConversation}
        onDeleteConversation={handleDeleteConversation}
        onOpenSettings={() => setView('settings')}
        onLogout={demoMode ? null : handleLogout}
      />

      <main className="app-main">
        {view === 'welcome' && (
          <WelcomeView user={user} onPick={handleSend} mode={chatMode}
            onModeChange={handleModeChange} busy={busy} onOpenAnalysis={openAnalysis} />
        )}
        {view === 'chat' && (
          <ChatView
            mode={chatMode}
            onModeChange={handleModeChange}
            onOpenAnalysis={openAnalysis}
            messages={messages}
            imageUrls={imageUrls}
            onSend={handleSend}
            onDownloadPdf={handleDownloadPdf}
            busy={busy}
            title={activeConv?.title}
            notConfigured={notConfigured}
            onOpenSettings={() => setView('settings')}
          />
        )}
        {view === 'report' && (
          <ReportView
            onGenerate={handleGenerate}
            onDownloadPdf={handleDownloadPdf}
            busy={busy}
            notConfigured={notConfigured}
            onOpenSettings={() => setView('settings')}
          />
        )}
        {view === 'settings' && (
          <SettingsView
            user={user}
            llm={llm}
            onSaved={(saved) => setLlm(saved)}
            onClose={() => setView(activeId ? 'chat' : 'welcome')}
            onLogout={demoMode ? null : handleLogout}
          />
        )}
      </main>

      <DebugDrawer />
    </div>
  )
}
