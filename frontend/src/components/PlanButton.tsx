import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Sprout } from 'lucide-react'
import { useNavigate } from 'react-router'
import { api, json, type Plan } from '../lib/api'
import { useMe } from '../lib/session'
import { useToast } from '../lib/toast'

/** A skill gap as a chip that asks the Coach for a growth plan. Sessions opened from an Apply
 * link only see their job, so there it stays a plain chip. */
export function SkillGap({ skill, jobId }: { skill: string; jobId: number }) {
  const { data: me } = useMe()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const make = useMutation({
    mutationFn: () => api<Plan>('/api/plans', { method: 'POST', body: json({ skill, job_id: jobId }) }),
    onSuccess: (plan) => {
      void qc.invalidateQueries({ queryKey: ['plans'] })
      toast(`The Coach has a plan for ${plan.skill}.`)
      void navigate(`/plans#plan-${plan.id}`)
    },
    onError: (err) => toast(err.message, 'error'),
  })

  const chip = 'inline-flex items-center gap-1 rounded-full border-2 border-ink px-2 py-0.5 text-xs font-semibold'
  if (!me || me.scope_job_id != null) return <span className={`${chip} bg-white`}>{skill}</span>
  return (
    <button
      type="button"
      onClick={() => make.mutate()}
      disabled={make.isPending}
      className={`${chip} bg-white hover:bg-buttercup disabled:opacity-60`}
      title={`Ask the Coach for a project that proves ${skill}`}
    >
      <Sprout className="size-3.5" aria-hidden />
      {skill}
      <span className="sr-only">: make a growth plan</span>
    </button>
  )
}
