// Shared brand assets: logo lockup + company byline.
// The logo file lives in frontend/public/brand (served as-is in dev and prod).
export const LOGO_SRC = '/brand/z-sheet.png'

export const COMPANY_NAME = '西安栈上月明软件科技有限公司'

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
