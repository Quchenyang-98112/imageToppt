import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '图转可编辑 PPT',
  description: '将 AI 生成的幻灯片图片转为可编辑画布与 PPTX',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
