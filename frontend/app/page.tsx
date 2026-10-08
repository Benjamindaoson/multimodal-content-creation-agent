import Link from 'next/link'

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-24">
      <div className="z-10 max-w-5xl w-full items-center justify-between font-mono text-sm">
        <h1 className="text-6xl font-bold text-center mb-8 bg-gradient-to-r from-blue-500 to-green-500 bg-clip-text text-transparent">
          Growth Flywheel 2.5
        </h1>
        <p className="text-center text-xl text-muted-foreground mb-12">
          AI 驱动的内容策略进化引擎
        </p>

        {/* CTA Buttons */}
        <div className="flex justify-center gap-4 mb-12">
          <Link
            href="/control-center"
            className="rounded-md bg-purple-600 px-6 py-3 text-sm font-medium text-white hover:bg-purple-500"
          >
            功能控制中心
          </Link>
          <Link
            href="/video-repurposing"
            className="rounded-md bg-sky-600 px-6 py-3 text-sm font-medium text-white hover:bg-sky-500"
          >
            直播智能切片
          </Link>
          <Link
            href="/login"
            className="rounded-md bg-primary px-6 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            登录
          </Link>
          <Link
            href="/register"
            className="rounded-md bg-secondary px-6 py-3 text-sm font-medium hover:bg-secondary/80"
          >
            注册
          </Link>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="p-6 border border-border rounded-lg bg-card hover:bg-card/80 transition-colors">
            <h3 className="text-lg font-semibold mb-2">🎯 策略对齐</h3>
            <p className="text-sm text-muted-foreground">
              通过对话引导，精准定位内容目标
            </p>
          </div>
          <div className="p-6 border border-border rounded-lg bg-card hover:bg-card/80 transition-colors">
            <h3 className="text-lg font-semibold mb-2">🚀 内容驾驶舱</h3>
            <p className="text-sm text-muted-foreground">
              双屏布局，生产与治理一体化
            </p>
          </div>
          <div className="p-6 border border-border rounded-lg bg-card hover:bg-card/80 transition-colors">
            <h3 className="text-lg font-semibold mb-2">📊 策略进化</h3>
            <p className="text-sm text-muted-foreground">
              GRPO 驱动，持续优化内容策略
            </p>
          </div>
        </div>
      </div>
    </main>
  )
}
