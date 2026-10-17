// Shared brand assets: logo lockup + company byline.
// The logo file lives in frontend/public/brand (served as-is in dev and prod).
export const LOGO_SRC = '/brand/z-sheet.png'

export const COMPANY_NAME = '西安栈上月明软件科技有限公司'

export const COMPANY_URL = 'https://zsoftym.com/'

interface BrandLockupProps {
  logoSize?: number
  className?: string
}

export function BrandLockup({ logoSize = 30, className = '' }: BrandLockupProps) {
  return (
    <span className={`brand-lockup${className ? ` ${className}` : ''}`}>
      <img
        src={LOGO_SRC}
        width={logoSize}
        height={logoSize}
        alt=""
        className="brand-logo"
      />
      <span className="brand-wordmark">Z-Sheet</span>
    </span>
  )
}

export function CompanyByline({ className = '' }: { className?: string }) {
  return (
    <p className={`company-byline${className ? ` ${className}` : ''}`}>
      © 2026 {COMPANY_NAME}
    </p>
  )
}

export function CompanyFooter() {
  const year = new Date().getFullYear()
  return (
    <footer className="app-footer">
      <div className="app-footer-inner">
        <span className="app-footer-product">
          <img src={LOGO_SRC} alt="" width={18} height={18} />
          <span>Z-Sheet</span>
          <span className="app-footer-dot" aria-hidden="true">·</span>
          <span>© {year}</span>
        </span>
        <a
          className="app-footer-company"
          href={COMPANY_URL}
          target="_blank"
          rel="noreferrer"
        >
          {COMPANY_NAME}
          <span className="app-footer-ext" aria-hidden="true">↗</span>
        </a>
      </div>
    </footer>
  )
}
