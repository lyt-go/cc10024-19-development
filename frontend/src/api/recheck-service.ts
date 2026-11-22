/** 违规复检前端数据层：首轮失败明细、清单选集、复检轮次与字典版本。 */
import { request } from './client'

export type Violation = {
  violation_id: string
  来源: string
  站点: string
  行标识: string
  source_row_id: number
  字段名: string
  原值: string
  归一值: string
  违规原因: string[]
  首轮字典版本: string
  状态: string
}

export type ChecklistItem = Violation & {
  当前值: string
  复检结果: string
  复检轮次: string
  复检字典版本: string
  当前字典版本: string | null
  口径冲突: boolean
  字典冲突明细: {
    原码: string
    旧字典版本: string
    旧值: string | null
    新字典版本: string
    新值: string | null
  } | null
}

export type Checklist = {
  id: number
  清单编号: string
  名称: string
  备注: string
  首轮轮次: string
  首轮字典版本: string
  字典字段: string
  选中: string[]
  选中站点: string[]
  创建时间: string
}

export type RoundChoice = {
  violation_id: string
  来源: string
  行标识: string
  字段名: string
  原值: string
  归一值: string
  首轮字典版本: string
  实际修复字典版本: string
  复检结果: string
  复检违规原因: string[]
  原违规规则: { 规则编号: string; 规则版本: string }
}

export type Round = {
  id: number
  轮次编号: string
  清单编号: string
  运行记录id: number | null
  执行方式: string
  首轮字典版本: string
  实际修复字典版本: string
  选择: RoundChoice[]
  统计: { 行数: number; 通过数: number; 仍违规数: number }
  口径冲突: {
    原码: string
    旧字典版本: string
    旧值: string | null
    新字典版本: string
    新值: string | null
  }[]
  重放自: string
  与上次一致?: boolean
  创建时间: string
}

export type DictionaryVersion = {
  版本: string
  映射: Record<string, string>
  生效: boolean
  备注: string
}

export type RecheckState = {
  字典字段: string
  首轮字典版本: string
  当前字典: DictionaryVersion | null
  字典版本: DictionaryVersion[]
  首轮违规数: number
  首轮来源: string[]
  清单数: number
  轮次数: number
  累计通过: number
  累计仍违规: number
}

type Page<T> = { items: T[]; total: number; page: number; size: number }

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `接口返回 ${response.status}`)
  }
  return (await response.json()) as T
}

export function loadState(): Promise<RecheckState> {
  return json('/api/recheck/state')
}

export function listViolations(params: Record<string, string | number>): Promise<Page<Violation>> {
  return json(`/api/recheck/violations?${new URLSearchParams(params as Record<string, string>)}`)
}

export function listChecklists(): Promise<{ items: Checklist[] }> {
  return json('/api/recheck/checklists')
}

export function createChecklist(body: {
  name: string
  violation_ids: string[]
  remark: string
}): Promise<{ ok: boolean; message: string; entry?: Checklist }> {
  return json('/api/recheck/checklists', { method: 'POST', body: JSON.stringify(body) })
}

export function listChecklistItems(
  checklistId: number,
  params: Record<string, string | number>,
): Promise<{ checklist: Checklist; items: ChecklistItem[]; total: number }> {
  return json(
    `/api/recheck/checklists/${checklistId}/items?${new URLSearchParams(params as Record<string, string>)}`,
  )
}

export function submitChecklist(
  checklistId: number,
  body: { violation_ids: string[]; mode: string; remark?: string },
): Promise<{ ok: boolean; message: string; entry?: Round }> {
  return json(`/api/recheck/checklists/${checklistId}/submit`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function listRounds(): Promise<{ items: Round[] }> {
  return json('/api/recheck/rounds')
}

export function replayRound(roundId: number): Promise<{ ok: boolean; message: string; entry?: Round }> {
  return json(`/api/recheck/rounds/${roundId}/replay`, { method: 'POST', body: '{}' })
}

export function loadDictionary(): Promise<{ active: DictionaryVersion | null; versions: DictionaryVersion[] }> {
  return json('/api/recheck/dictionary')
}

export function installDictionary(body: {
  version: string
  mapping: Record<string, string>
  note: string
}): Promise<{ ok: boolean; message: string }> {
  return json('/api/recheck/dictionary', { method: 'POST', body: JSON.stringify(body) })
}

export async function runHistoryRecheck(checklistId: number): Promise<{ ok: boolean; message: string }> {
  const created = await json<{ ok: boolean; message: string; entry?: { id: number } }>('/api/run_history', {
    method: 'POST',
    body: JSON.stringify({
      values: {
        记录编号: `RUN-RCK-${Date.now().toString().slice(-6)}`,
        关联计划: 'SCHE-REGION',
        开始时间: new Date().toISOString().slice(0, 19),
        复检清单id: checklistId,
      },
    }),
  })
  if (!created.ok || !created.entry) return { ok: false, message: created.message }
  const action = await json<{ ok: boolean; message: string }>(
    `/api/run_history/${created.entry.id}/actions`,
    { method: 'POST', body: JSON.stringify({ action: '确认完成' }) },
  )
  return { ok: action.ok, message: `运行记录 #${created.entry.id}：${action.message}` }
}
