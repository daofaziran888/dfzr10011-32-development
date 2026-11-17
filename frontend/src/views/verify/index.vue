<template>
  <section class="page" data-module="verify">
    <header class="page-head">
      <div>
        <h2>采集结果校验管理</h2>
        <p class="page-desc">维护校验规则归属、版本审批与放行结果：规则只能由采集计划所属团队创建或修改，未获批版本不能产生放行结论。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!session.canOperate" @click="openCreate">登记校验规则</button>
        <button class="btn" type="button" @click="exportRows">导出采集结果校验清单</button>
        <RouterLink class="btn ghost" to="/merge?tab=review">前往归属复核清单</RouterLink>
      </div>
    </header>

    <div v-if="!session.canOperate" class="banner">
      当前身份为只读（{{ session.team }} · {{ session.role }}），只能查看共享内容，不能改动。
    </div>

    <div class="tabs">
      <button class="tab" :class="{ active: activeTab === 'rules' }" type="button" @click="switchTab('rules')">校验规则</button>
      <button class="tab" :class="{ active: activeTab === 'release' }" type="button" @click="switchTab('release')">校验放行结果数据</button>
    </div>

    <template v-if="activeTab === 'rules'">
      <div class="stat-row">
        <article v-for="item in stats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>

      <form class="filter-bar" @submit.prevent="reload">
        <label class="filter-item">
          <span>规则编号</span>
          <input v-model="filters.keyword" placeholder="按规则编号检索" />
        </label>
        <label class="filter-item">
          <span>规则状态</span>
          <select v-model="filters.status">
            <option value="">全部</option>
            <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <div v-if="showForm" class="panel">
        <h3>{{ formMode === 'create' ? `登记校验规则（归属团队：${session.team}）` : formMode === 'version' ? `为 ${formRule?.规则编号} 登记新版本` : `保存 ${formRule?.规则编号} 草稿版本` }}</h3>
        <div class="form-grid">
          <label>
            采集计划
            <select v-model="form.采集计划" :disabled="formMode !== 'create'">
              <option value="">请选择</option>
              <option
                v-for="plan in plans"
                :key="plan.计划编号"
                :value="plan.计划编号"
                :disabled="plan.所属团队 !== session.team"
              >
                {{ plan.计划编号 }} · {{ plan.计划名称 }}（{{ plan.所属团队 }}）
              </option>
            </select>
          </label>
          <label>
            校验类型
            <select v-model="form.校验类型">
              <option v-for="item in checkTypes" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>
          <label>
            阈值配置
            <input v-model="form.阈值配置" placeholder="数值下限/长度上限必填" />
          </label>
          <label>
            不通过动作
            <select v-model="form.不通过动作">
              <option v-for="item in rejectActions" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>
        </div>
        <div class="field-picks">
          <span>校验字段（必须指向计划真实输出）：</span>
          <label v-for="field in currentPlanOutputs" :key="field">
            <input v-model="form.校验字段" type="checkbox" :value="field" /> {{ field }}
          </label>
          <span v-if="!currentPlanOutputs.length" class="muted">请先选择采集计划</span>
        </div>
        <div class="panel-actions">
          <button class="btn primary" type="button" @click="submitForm">{{ formMode === 'draft' ? '保存草稿' : '提交登记' }}</button>
          <button class="btn ghost" type="button" @click="closeForm">取消</button>
        </div>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">
              <button v-if="column === '规则编号'" class="link" type="button" @click="openDetail(row)">{{ row[column] }}</button>
              <span v-else-if="column === '审批状态'">
                <span class="badge" :class="approvalBadge(row.审批状态)">{{ row.审批状态 ?? '—' }}</span>
              </span>
              <span v-else>{{ displayCell(row, column) }}</span>
            </td>
            <td class="row-actions">
              <button
                v-for="action in actionsFor(row)"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
              <span v-if="!actionsFor(row).length" class="muted">仅可查看</span>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 1" class="empty-state">暂无采集结果校验数据，可先登记校验规则</td>
          </tr>
        </tbody>
      </table>

      <div v-if="detail" class="panel">
        <h3>规则 {{ detail.规则编号 }} 版本与审批明细（采集计划 {{ detail.采集计划 }} · 计划所属团队 {{ detail.计划所属团队 }}）</h3>
        <h4>版本列表（未获批版本只读，仅草稿可保存修改）</h4>
        <table class="data-table">
          <thead>
            <tr><th>版本号</th><th>审批状态</th><th>校验类型</th><th>校验字段</th><th>阈值配置</th><th>不通过动作</th><th>提交团队</th><th>提交时间</th><th>审批团队</th><th>审批时间</th><th>只读</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="version in detail.版本列表" :key="version.版本号">
              <td>v{{ version.版本号 }}</td>
              <td><span class="badge" :class="approvalBadge(version.审批状态)">{{ version.审批状态 }}</span></td>
              <td>{{ version.校验类型 }}</td>
              <td>{{ (version.校验字段 || []).join('、') }}</td>
              <td>{{ version.阈值配置 ?? '—' }}</td>
              <td>{{ version.不通过动作 }}</td>
              <td>{{ version.提交团队 }}</td>
              <td>{{ version.提交时间 ?? '—' }}</td>
              <td>{{ version.审批团队 ?? '—' }}</td>
              <td>{{ version.审批时间 ?? '—' }}</td>
              <td><span class="badge" :class="version.只读 ? 'muted' : 'ok'">{{ version.只读 ? '只读' : '可编辑' }}</span></td>
              <td>
                <button
                  v-if="!version.只读 && session.canOperate && detail.计划所属团队 === session.team"
                  class="link"
                  type="button"
                  @click="openDraftForm(version)"
                >保存草稿</button>
                <span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <h4>审批记录（撤回审批不删除已有不通过明细）</h4>
        <table class="data-table">
          <thead>
            <tr><th>版本号</th><th>审批团队</th><th>审批结论</th><th>审批时间</th><th>审批意见</th><th>交接时间</th><th>原审批撤回依据</th><th>已撤回</th><th>撤回依据</th><th>撤回时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="(record, index) in detail.审批记录" :key="index">
              <td>v{{ record.版本号 }}</td>
              <td>{{ record.审批团队 }}</td>
              <td>{{ record.审批结论 }}</td>
              <td>{{ record.审批时间 }}</td>
              <td>{{ record.审批意见 ?? '—' }}</td>
              <td>{{ record.交接时间 ?? '—' }}</td>
              <td>{{ record.原审批撤回依据 ?? '—' }}</td>
              <td>{{ record.已撤回 ? '是' : '否' }}</td>
              <td>{{ record.撤回依据 ?? '—' }}</td>
              <td>{{ record.撤回时间 ?? '—' }}</td>
            </tr>
            <tr v-if="!detail.审批记录.length"><td colspan="10" class="empty-state">暂无审批记录</td></tr>
          </tbody>
        </table>
        <h4>计划交接记录</h4>
        <table class="data-table">
          <thead>
            <tr><th>原团队</th><th>新团队</th><th>交接时间</th><th>交接说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="(record, index) in detail.交接记录" :key="index">
              <td>{{ record.原团队 }}</td>
              <td>{{ record.新团队 }}</td>
              <td>{{ record.交接时间 }}</td>
              <td>{{ record.交接说明 }}</td>
            </tr>
            <tr v-if="!detail.交接记录.length"><td colspan="4" class="empty-state">暂无交接记录</td></tr>
          </tbody>
        </table>
      </div>

      <footer class="page-foot">
        <span>共 {{ total }} 条采集结果校验记录</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
        <span v-else-if="successMessage" class="success-text">{{ successMessage }}</span>
      </footer>
    </template>

    <template v-else>
      <form class="filter-bar" @submit.prevent="reloadRelease">
        <label class="filter-item">
          <span>采集计划</span>
          <select v-model="releaseFilters.plan">
            <option value="">全部</option>
            <option v-for="plan in plans" :key="plan.计划编号" :value="plan.计划编号">{{ plan.计划编号 }} · {{ plan.计划名称 }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>校验结论</span>
          <select v-model="releaseFilters.conclusion">
            <option value="">全部</option>
            <option value="放行">放行</option>
            <option value="不通过">不通过</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetReleaseFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in releaseColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in releaseRows" :key="String(row.id)">
            <td v-for="column in releaseColumns" :key="column">
              <span v-if="column === '校验结论'" class="badge" :class="row[column] === '放行' ? 'ok' : 'err'">{{ row[column] }}</span>
              <span v-else>{{ row[column] ?? '—' }}</span>
            </td>
          </tr>
          <tr v-if="!releaseRows.length">
            <td :colspan="releaseColumns.length" class="empty-state">暂无校验放行结果，需先让规则版本获批并执行校验</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ releaseTotal }} 条校验放行结果</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, any>

const ENDPOINT = '/api/verify'
const session = useSessionStore()
const route = useRoute()

const columns = ["规则编号", "采集计划", "归属团队", "计划所属团队", "当前版本", "审批状态", "校验类型", "校验字段", "阈值配置", "不通过动作", "最近校验", "规则状态"]
const releaseColumns = ["结果编号", "规则编号", "版本号", "采集计划", "计划所属团队", "审批团队", "交接时间", "原审批撤回依据", "明细编号", "校验字段", "校验类型", "阈值配置", "校验结论", "不通过动作", "生成时间"]
const statuses = ["草稿", "待审批", "已启用", "已暂停", "已关闭"]
const checkTypes = ["非空校验", "数值下限", "长度上限"]
const rejectActions = ["隔离明细", "告警通知", "拦截入库"]

const activeTab = ref(route.query.tab === 'release' ? 'release' : 'rules')
const rows = ref<Row[]>([])
const total = ref(0)
const plans = ref<Row[]>([])
const detail = ref<Row | null>(null)
const releaseRows = ref<Row[]>([])
const releaseTotal = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', status: '' })
const releaseFilters = ref({ plan: '', conclusion: '' })

const showForm = ref(false)
const formMode = ref<'create' | 'version' | 'draft'>('create')
const formRule = ref<Row | null>(null)
const form = ref<Record<string, any>>({ 采集计划: '', 校验类型: '非空校验', 校验字段: [], 阈值配置: '', 不通过动作: '隔离明细' })

const stats = computed(() => [
  { label: '校验规则总数', value: total.value },
  { label: '待审批规则', value: rows.value.filter((row) => row.审批状态 === '待审批').length },
  { label: '已启用规则', value: rows.value.filter((row) => row.status === '已启用').length },
  { label: '放行结果数据', value: releaseTotal.value },
])

const currentPlanOutputs = computed(() => {
  const plan = plans.value.find((item) => item.计划编号 === form.value.采集计划)
  return (plan?.输出字段 as string[]) ?? []
})

function operatorPayload() {
  return { 操作人: session.operator, 操作团队: session.team, 操作角色: session.role }
}

function displayCell(row: Row, column: string) {
  const value = row[column]
  if (Array.isArray(value)) return value.join('、')
  return value ?? '—'
}

function approvalBadge(status: unknown) {
  if (status === '已获批') return 'ok'
  if (status === '待审批') return 'warn'
  if (status === '已驳回' || status === '已撤回') return 'err'
  return 'muted'
}

function actionsFor(row: Row): string[] {
  if (!session.canOperate) return []
  const owner = row.计划所属团队 === session.team
  const list: string[] = []
  if (row.审批状态 === '草稿' && owner) list.push('提交审批')
  if (row.审批状态 === '待审批') list.push('审批通过', '审批不通过')
  if (row.已获批版本) {
    if (owner) list.push('执行校验')
    list.push('撤回审批')
  }
  if (!row.在途版本 && owner && row.status !== '已关闭') list.push('新版本')
  if (row.status === '已启用' && owner) list.push('暂停校验')
  if (row.status === '已暂停' && owner) list.push('恢复校验', '关闭校验')
  return list
}

function switchTab(tab: string) {
  activeTab.value = tab
  if (tab === 'release') void reloadRelease()
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function resetReleaseFilters() {
  releaseFilters.value = { plan: '', conclusion: '' }
  void reloadRelease()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  formMode.value = 'create'
  formRule.value = null
  form.value = { 采集计划: '', 校验类型: '非空校验', 校验字段: [], 阈值配置: '', 不通过动作: '隔离明细' }
  showForm.value = true
}

function openVersionForm(row: Row) {
  formMode.value = 'version'
  formRule.value = row
  form.value = { 采集计划: row.采集计划, 校验类型: row.校验类型, 校验字段: [...(row.校验字段 ?? [])], 阈值配置: row.阈值配置 ?? '', 不通过动作: row.不通过动作 }
  showForm.value = true
}

function openDraftForm(version: Row) {
  formMode.value = 'draft'
  form.value = { 采集计划: detail.value?.采集计划, 校验类型: version.校验类型, 校验字段: [...(version.校验字段 ?? [])], 阈值配置: version.阈值配置 ?? '', 不通过动作: version.不通过动作 }
  showForm.value = true
}

function closeForm() {
  showForm.value = false
  formRule.value = null
}

async function submitForm() {
  errorMessage.value = ''
  successMessage.value = ''
  const values = { ...form.value, ...operatorPayload() }
  try {
    let response: Response
    if (formMode.value === 'create') {
      response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values }) })
    } else if (formMode.value === 'version' && formRule.value) {
      response = await request(`${ENDPOINT}/${formRule.value.id}/versions`, { method: 'POST', body: JSON.stringify({ values }) })
    } else if (formMode.value === 'draft' && detail.value) {
      response = await request(`${ENDPOINT}/${detail.value.id}/actions`, { method: 'POST', body: JSON.stringify({ values: { ...values, action: '保存草稿' } }) })
    } else {
      return
    }
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '校验规则登记失败')
    successMessage.value = payload.message
    closeForm()
    await reload()
    if (detail.value) await openDetail(detail.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '校验规则登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  if (action === '新版本') {
    openVersionForm(row)
    return
  }
  const values: Record<string, any> = { action, ...operatorPayload() }
  if (action === '审批通过' || action === '审批不通过') {
    const opinion = window.prompt(`请填写${action}意见`, action === '审批通过' ? '同意' : '')
    if (opinion === null) return
    values.审批意见 = opinion
  }
  if (action === '撤回审批') {
    const basis = window.prompt('请填写撤回依据（必填，已有不通过明细会保留）', '')
    if (!basis) {
      errorMessage.value = '撤回审批必须填写撤回依据'
      return
    }
    values.撤回依据 = basis
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, { method: 'POST', body: JSON.stringify({ values }) })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '采集结果校验动作未生效')
    successMessage.value = payload.message
    await reload()
    if (detail.value?.id === row.id) await openDetail(row)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集结果校验操作失败'
  }
}

async function openDetail(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('校验规则明细读取失败')
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '校验规则明细读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.status) query.set('status', filters.value.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('校验规则列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集结果校验列表读取失败'
  }
}

async function reloadRelease() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (releaseFilters.value.plan) query.set('plan', releaseFilters.value.plan)
  if (releaseFilters.value.conclusion) query.set('conclusion', releaseFilters.value.conclusion)
  try {
    const response = await request(`${ENDPOINT}/release?${query.toString()}`)
    if (!response.ok) throw new Error('校验放行结果读取失败')
    const payload = await response.json()
    releaseRows.value = payload.items ?? []
    releaseTotal.value = payload.total ?? releaseRows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '校验放行结果读取失败'
  }
}

async function reloadPlans() {
  try {
    const response = await request(`${ENDPOINT}/plans`)
    if (!response.ok) throw new Error('采集计划归属读取失败')
    const payload = await response.json()
    plans.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集计划归属读取失败'
  }
}

watch(() => route.query.tab, (tab) => {
  activeTab.value = tab === 'release' ? 'release' : 'rules'
  if (tab === 'release') void reloadRelease()
})

onMounted(async () => {
  await Promise.all([reload(), reloadPlans(), reloadRelease()])
})
</script>
