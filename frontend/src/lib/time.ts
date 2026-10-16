// Dates shown as "3 days ago", in the reader's locale.

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

export function postedLabel(iso: string): string {
  const days = Math.round((new Date(iso).getTime() - Date.now()) / 86_400_000)
  if (days > -1) return 'today'
  if (days > -30) return relative.format(days, 'day')
  return relative.format(Math.round(days / 30), 'month')
}
