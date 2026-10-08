'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { href: '/control-center', label: '控制中心' },
  { href: '/trend-radar', label: '热点雷达' },
  { href: '/publish', label: '发布管理' },
  { href: '/video-repurposing', label: '直播切片' },
];

export function TopNav() {
  const pathname = usePathname();

  return (
    <nav className="border-b border-white/[0.06] bg-black/80 backdrop-blur-xl sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-6 h-12 flex items-center gap-1">
        <Link href="/" className="text-sm font-bold text-white/90 mr-6">
          Growth Flywheel
        </Link>
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              'px-3 py-1.5 rounded-md text-sm transition-colors',
              pathname === item.href
                ? 'bg-white/10 text-white'
                : 'text-white/40 hover:text-white/70 hover:bg-white/[0.04]'
            )}
          >
            {item.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
