<template>
  <section class="page" data-module="violation_recheck">
    <header class="page-head">
      <div>
        <h2>违规复检</h2>
        <p class="page-desc">
          按首轮 Schema 失败明细建立复检清单，只重算选中的来源行；翻页、筛选和提交都依据稳定的违规身份。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="run-history-recheck" type="button" @click="runViaHistory">
          用运行历史执行本轮复检
        </button>
        <button class="btn" data-testid="replay-latest" type="button" @click="replayLatest">
          重放最近轮次
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">首轮违规</span>
        <strong class="stat-value" data-testid="first-round-count">{{ state?.首轮违规数 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">首轮字典版本</span>
        <strong class="stat-value" data-testid="first-dict-version">{{ state?.首轮字典版本 ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">当前生效字典</span>
        <strong class="stat-value" data-testid="active-dict-version">{{ state?.当前字典?.版本 ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">累计通过 / 仍违规</span>
        <strong class="stat-value" data-testid="round-totals">
          {{ state?.累计通过 ?? 0 }} / {{ state?.累计仍违规 ?? 0 }}
        </strong>
      </article>
    </div>

    <p class="status-legend">
      <span class="legend-item">首轮来源：{{ (state?.首轮来源 ?? []).join('、') || '—' }}</span>
      <span class="legend-item">清单数：{{ state?.清单数 ?? 0 }}，轮次数：{{ state?.轮次数 ?? 0 }}</span>
    </p>

    <section class="recheck-grid">
      <div class="recheck-panel">
        <h3>首轮失败明细</h3>
        <form class="filter-bar" @submit.prevent="reloadFirstRound(1)">
          <label class="filter-item">
            <span>来源</span>
            <input v-model="firstFilters.source" data-testid="first-filter-source" placeholder="按来源检索" />
          </label>
          <label class="filter-item">
            <span>字段</span>
            <input v-model="firstFilters.field" data-testid="first-filter-field" placeholder="按字段检索" />
          </label>
          <label class="filter-item">
            <span>排序</span>
            <select v-model="firstFilters.sort" data-testid="first-filter-sort">
              <option value="来源,行标识">来源、行标识升序</option>
              <option value="-来源,行标识">来源降序、行标识升序</option>
              <option value="行标识">仅按行标识</option>
            </select>
          </label>
          <button class="btn" data-testid="first-query" type="submit">查询</button>
        </form>
        <table class="data-table">
          <thead>
            <tr>
              <th><input v-model="allFirst" type="checkbox" data-testid="first-select-all" @change="toggleAllFirst" /></th>
              <th>来源</th><th>行标识</th><th>字段</th><th>原值</th><th>违规原因</th><th>字典版本</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in firstViolations" :key="row.violation_id" :data-testid="`first-${row.violation_id}`">
              <td>
                <input v-model="picked" type="checkbox" :value="row.violation_id" :data-testid="`first-pick-${row.violation_id}`" />
              </td>
              <td>{{ row.来源 }}</td>
              <td>{{ row.行标识 }}</td>
              <td>{{ row.字段名 }}</td>
              <td>{{ row.原值 }}</td>
              <td>{{ row.违规原因.join('；') }}</td>
              <td>{{ row.首轮字典版本 }}</td>
            </tr>
            <tr v-if="!firstViolations.length">
              <td colspan="7" class="empty-state">当前筛选下没有首轮违规明细</td>
            </tr>
          </tbody>
        </table>
        <div class="page-foot">
          <span>共 {{ firstTotal }} 条，第 {{ firstPage }} 页</span>
          <span class="page-actions">
            <button class="btn ghost" type="button" :disabled="firstPage <= 1" @click="reloadFirstRound(firstPage - 1)">上一页</button>
            <button class="btn ghost" type="button" :disabled="firstPage * firstSize >= firstTotal" @click="reloadFirstRound(firstPage + 1)">下一页</button>
          </span>
        </div>
        <div class="page-actions">
          <input v-model="checklistName" class="inline-input" data-testid="checklist-name" placeholder="清单名称（可选）" />
          <button class="btn primary" data-testid="create-checklist" type="button" @click="createChecklistFromPicked">
            用勾选身份建复检清单（{{ picked.length }}）
          </button>
        </div>
      </div>

      <div class="recheck-panel">
        <h3>复检清单</h3>
        <ul class="checklist-list">
          <li
            v-for="item in checklists"
            :key="item.id"
            :class="{ active: item.id === activeChecklistId }"
            :data-testid="`checklist-${item.清单编号}`"
            @click="selectChecklist(item.id)"
          >
            <strong>{{ item.清单编号 }}</strong>
            <span>{{ item.名称 }}</span>
            <span class="muted">站点：{{ item.选中站点.join('、') }}；选中 {{ item.选中.length }} 条</span>
          </li>
          <li v-if="!checklists.length" class="empty-state">还没有复检清单，请先从首轮明细勾选并创建</li>
        </ul>

        <form v-if="activeChecklistId" class="filter-bar" @submit.prevent="reloadChecklistItems(1)">
          <label class="filter-item">
            <span>来源</span>
            <input v-model="itemFilters.source" data-testid="item-filter-source" placeholder="按来源检索" />
          </label>
          <label class="filter-item">
            <span>排序</span>
            <select v-model="itemFilters.sort" data-testid="item-filter-sort">
              <option value="来源,行标识">来源、行标识升序</option>
              <option value="-来源,行标识">来源降序、行标识升序</option>
            </select>
          </label>
          <button class="btn" data-testid="item-query" type="submit">查询</button>
        </form>

        <table v-if="activeChecklistId" class="data-table">
          <thead>
            <tr>
              <th><input v-model="allItems" type="checkbox" data-testid="item-select-all" @change="toggleAllItems" /></th>
              <th>来源</th><th>行标识</th><th>原值</th><th>当前值</th><th>复检结果</th><th>口径冲突</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in checklistItems" :key="row.violation_id" :data-testid="`item-${row.violation_id}`">
              <td>
                <input v-model="pickedItems" type="checkbox" :value="row.violation_id" :data-testid="`item-pick-${row.violation_id}`" />
              </td>
              <td>{{ row.来源 }}</td>
              <td>{{ row.行标识 }}</td>
              <td>{{ row.原值 }}</td>
              <td>{{ row.当前值 || row.归一值 || '—' }}</td>
              <td>{{ row.复检结果 }}</td>
              <td>
                <span v-if="row.口径冲突" class="conflict" data-testid="caliber-conflict">
                  {{ row.字典冲突明细?.旧值 ?? '—' }} → {{ row.字典冲突明细?.新值 ?? '缺项' }}
                </span>
                <span v-else class="muted">—</span>
              </td>
            </tr>
            <tr v-if="!checklistItems.length">
              <td colspan="7" class="empty-state">该清单在筛选下没有明细</td>
            </tr>
          </tbody>
        </table>
        <div v-if="activeChecklistId" class="page-foot">
          <span>共 {{ itemTotal }} 条，第 {{ itemPage }} 页</span>
          <span class="page-actions">
            <button class="btn ghost" type="button" :disabled="itemPage <= 1" @click="reloadChecklistItems(itemPage - 1)">上一页</button>
            <button class="btn ghost" type="button" :disabled="itemPage * itemSize >= itemTotal" @click="reloadChecklistItems(itemPage + 1)">下一页</button>
          </span>
        </div>
        <div v-if="activeChecklistId" class="page-actions">
          <label class="filter-item">
            <span>复检口径</span>
            <select v-model="submitMode" data-testid="submit-mode">
              <option value="current">按当前字典版本</option>
              <option value="first">按首轮字典版本</option>
            </select>
          </label>
          <button class="btn primary" data-testid="submit-selected" type="button" @click="submitSelected">
            提交复检选中行（{{ pickedItems.length }}）
          </button>
        </div>
      </div>
    </section>

    <section class="recheck-panel">
      <h3>字典版本</h3>
      <table class="data-table">
        <thead><tr><th>版本</th><th>生效</th><th>映射</th><th>备注</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="version in dictionary.versions" :key="version.版本" :data-testid="`dict-${version.版本}`">
            <td>{{ version.版本 }}</td>
            <td>{{ version.生效 ? '是' : '' }}</td>
            <td class="mono">{{ formatMapping(version.映射) }}</td>
            <td>{{ version.备注 }}</td>
            <td>
              <button
                v-if="!version.生效"
                class="link"
                type="button"
                :data-testid="`dict-activate-${version.版本}`"
                @click="activateDictionary(version)"
              >设为当前</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="recheck-panel">
      <h3>复检记录</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>轮次</th><th>执行方式</th><th>清单</th><th>首轮字典</th><th>实际修复字典</th>
            <th>行数</th><th>通过</th><th>仍违规</th><th>重放自</th><th>一致性</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="round in rounds" :key="round.轮次编号" :data-testid="`round-${round.轮次编号}`">
            <td>{{ round.轮次编号 }}</td>
            <td>{{ round.执行方式 }}</td>
            <td>{{ round.清单编号 }}</td>
            <td>{{ round.首轮字典版本 }}</td>
            <td>{{ round.实际修复字典版本 }}</td>
            <td>{{ round.统计.行数 }}</td>
            <td>{{ round.统计.通过数 }}</td>
            <td>{{ round.统计.仍违规数 }}</td>
            <td>{{ round.重放自 || '—' }}</td>
            <td>{{ round.与上次一致 === undefined ? '—' : (round.与上次一致 ? '一致' : '不一致') }}</td>
            <td>
              <button class="link" type="button" :data-testid="`replay-${round.轮次编号}`" @click="replayRoundById(round.id)">重放</button>
            </td>
          </tr>
          <tr v-if="!rounds.length">
            <td colspan="11" class="empty-state">还没有复检记录</td>
          </tr>
        </tbody>
      </table>
      <div v-if="latestRound" class="round-detail" data-testid="latest-round-detail">
        <h4>最近一次：{{ latestRound.轮次编号 }}（{{ latestRound.执行方式 }}）</h4>
        <table class="data-table">
          <thead><tr><th>来源</th><th>行标识</th><th>原值</th><th>原违规规则</th><th>首轮字典</th><th>实际修复字典</th><th>归一值</th><th>结果</th></tr></thead>
          <tbody>
            <tr v-for="choice in latestRound.选择" :key="choice.violation_id">
              <td>{{ choice.来源 }}</td>
              <td>{{ choice.行标识 }}</td>
              <td>{{ choice.原值 }}</td>
              <td>{{ choice.原违规规则?.规则编号 ?? '—' }} / {{ choice.原违规规则?.规则版本 ?? '—' }}</td>
              <td>{{ choice.首轮字典版本 }}</td>
              <td>{{ choice.实际修复字典版本 }}</td>
              <td>{{ choice.归一值 }}</td>
              <td>{{ choice.复检结果 }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="latestRound.口径冲突.length" class="conflict" data-testid="round-conflicts">
          口径冲突：
          <span v-for="conflict in latestRound.口径冲突" :key="conflict.原码">
            {{ conflict.原码 }} 在 {{ conflict.旧字典版本 }} 为 {{ conflict.旧值 ?? '缺项' }}，
            在 {{ conflict.新字典版本 }} 为 {{ conflict.新值 ?? '缺项' }}；
          </span>
        </p>
      </div>
    </section>

    <footer class="page-foot">
      <span>{{ message }}</span>
      <span v-if="errorMessage" class="error-text" data-testid="recheck-error">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createChecklist,
  installDictionary,
  listChecklistItems,
  listChecklists,
  listRounds,
  listViolations,
  loadDictionary,
  loadState,
  replayRound,
  submitChecklist,
  runHistoryRecheck,
} from '@/api/recheck-service'
import type {
  Checklist,
  ChecklistItem,
  DictionaryVersion,
  RecheckState,
  Round,
  Violation,
} from '@/api/recheck-service'

const state = ref<RecheckState | null>(null)
const firstViolations = ref<Violation[]>([])
const firstTotal = ref(0)
const firstPage = ref(1)
const firstSize = ref(5)
const firstFilters = ref({ source: '', field: '', sort: '来源,行标识' })
const allFirst = ref(false)
const picked = ref<string[]>([])

const checklists = ref<Checklist[]>([])
const activeChecklistId = ref<number | null>(null)
const checklistItems = ref<ChecklistItem[]>([])
const itemTotal = ref(0)
const itemPage = ref(1)
const itemSize = ref(5)
const itemFilters = ref({ source: '', sort: '来源,行标识' })
const allItems = ref(false)
const pickedItems = ref<string[]>([])
const submitMode = ref('current')
const checklistName = ref('')

const dictionary = ref<{ versions: DictionaryVersion[] }>({ versions: [] })
const rounds = ref<Round[]>([])
const message = ref('')
const errorMessage = ref('')

const activeChecklist = computed(
  () => checklists.value.find((item) => item.id === activeChecklistId.value) ?? null,
)
const latestRound = computed(() => rounds.value[rounds.value.length - 1] ?? null)

function fail(error: unknown, fallback: string) {
  errorMessage.value = error instanceof Error ? error.message : fallback
}

function formatMapping(mapping: Record<string, string>) {
  return Object.entries(mapping).map(([code, value]) => `${code}→${value}`).join('，')
}

async function reloadState() {
  state.value = await loadState()
  dictionary.value = await loadDictionary()
}

function firstParams(page: number) {
  return {
    page,
    size: firstSize.value,
    sort: firstFilters.value.sort,
    ...(firstFilters.value.source ? { source: firstFilters.value.source } : {}),
    ...(firstFilters.value.field ? { field: firstFilters.value.field } : {}),
  }
}

async function reloadFirstRound(page = firstPage.value) {
  errorMessage.value = ''
  try {
    const payload = await listViolations(firstParams(page))
    firstViolations.value = payload.items
    firstTotal.value = payload.total
    firstPage.value = page
    allFirst.value = false
  } catch (error) {
    fail(error, '首轮失败明细读取失败')
  }
}

async function reloadChecklists() {
  checklists.value = (await listChecklists()).items
  if (activeChecklistId.value === null && checklists.value.length) {
    activeChecklistId.value = checklists.value[0].id
  }
}

function itemParams(page: number) {
  return {
    page,
    size: itemSize.value,
    sort: itemFilters.value.sort,
    ...(itemFilters.value.source ? { source: itemFilters.value.source } : {}),
  }
}

async function reloadChecklistItems(page = itemPage.value) {
  if (activeChecklistId.value === null) return
  errorMessage.value = ''
  try {
    const payload = await listChecklistItems(activeChecklistId.value, itemParams(page))
    checklistItems.value = payload.items
    itemTotal.value = payload.total
    itemPage.value = page
    allItems.value = false
    pickedItems.value = pickedItems.value.filter((id) =>
      payload.items.some((row) => row.violation_id === id) || pickedItems.value.includes(id),
    )
  } catch (error) {
    fail(error, '复检清单明细读取失败')
  }
}

async function reloadRounds() {
  rounds.value = (await listRounds()).items
}

function toggleAllFirst() {
  picked.value = allFirst.value ? firstViolations.value.map((row) => row.violation_id) : []
}

function toggleAllItems() {
  pickedItems.value = allItems.value
    ? checklistItems.value.map((row) => row.violation_id)
    : []
}

async function selectChecklist(id: number) {
  activeChecklistId.value = id
  pickedItems.value = []
  itemPage.value = 1
  await reloadChecklistItems(1)
}

async function createChecklistFromPicked() {
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await createChecklist({
      name: checklistName.value,
      violation_ids: picked.value,
      remark: '从首轮失败明细按违规身份勾选',
    })
    if (!result.ok) throw new Error(result.message)
    message.value = result.message
    picked.value = []
    await reloadChecklists()
    await reloadState()
    await selectChecklist(result.entry!.id)
  } catch (error) {
    fail(error, '复检清单创建失败')
  }
}

async function submitSelected() {
  if (activeChecklistId.value === null) return
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await submitChecklist(activeChecklistId.value, {
      violation_ids: pickedItems.value,
      mode: submitMode.value,
      remark: '运维台按选集提交',
    })
    if (!result.ok) throw new Error(result.message)
    message.value = result.message
    await Promise.all([reloadRounds(), reloadState(), reloadChecklistItems(itemPage.value)])
  } catch (error) {
    fail(error, '复检提交失败')
  }
}

async function replayRoundById(roundId: number) {
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await replayRound(roundId)
    if (!result.ok) throw new Error(result.message)
    message.value = result.message
    await Promise.all([reloadRounds(), reloadState()])
  } catch (error) {
    fail(error, '重放失败')
  }
}

async function replayLatest() {
  if (!latestRound.value) return
  await replayRoundById(latestRound.value.id)
}

async function activateDictionary(version: DictionaryVersion) {
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await installDictionary({
      version: version.版本,
      mapping: version.映射,
      note: version.备注,
    })
    if (!result.ok) throw new Error(result.message)
    message.value = result.message
    await Promise.all([reloadState(), reloadChecklistItems(itemPage.value)])
  } catch (error) {
    fail(error, '字典版本切换失败')
  }
}

async function runViaHistory() {
  if (activeChecklistId.value === null) {
    errorMessage.value = '请先选择一张复检清单'
    return
  }
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await runHistoryRecheck(activeChecklistId.value)
    if (!result.ok) throw new Error(result.message)
    message.value = result.message
    await Promise.all([reloadRounds(), reloadState(), reloadChecklistItems(itemPage.value), reloadFirstRound()])
  } catch (error) {
    fail(error, '运行历史复检失败')
  }
}

onMounted(async () => {
  try {
    await Promise.all([reloadState(), reloadFirstRound(1), reloadChecklists(), reloadRounds()])
    if (activeChecklistId.value !== null) await reloadChecklistItems(1)
  } catch (error) {
    fail(error, '违规复检数据读取失败')
  }
})
</script>

<style scoped>
.recheck-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.recheck-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 16px; }
.recheck-panel h3 { margin: 0 0 10px; font-size: 14px; }
.checklist-list { list-style: none; padding: 0; margin: 0 0 10px; display: flex; flex-direction: column; gap: 6px; }
.checklist-list li { border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.checklist-list li.active { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.muted { color: var(--muted); font-size: 12px; }
.conflict { color: #b42318; font-size: 12px; }
.mono { font-family: monospace; font-size: 12px; }
.inline-input { border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; }
.round-detail h4 { font-size: 13px; margin: 14px 0 8px; }
@media (max-width: 1100px) { .recheck-grid { grid-template-columns: 1fr; } }
</style>
