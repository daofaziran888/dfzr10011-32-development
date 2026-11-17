<template>
  <section class="page" data-module="merge">
    <header class="page-head">
      <div>
        <h2>结果聚合归并管理</h2>
        <p class="page-desc">维护聚合策略与输出管理；放行清单读取校验放行结果数据，只放行唯一获批规则版本产生的明细。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记聚合任务</button>
        <button class="btn" type="button" @click="exportRows">导出结果聚合归并清单</button>
        <RouterLink class="btn ghost" to="/verify?tab=release">前往校验放行结果</RouterLink>
      </div>
    </header>

    <div v-if="!session.canOperate" class="banner">
      当前身份为只读（{{ session.team }} · {{ session.role }}），只能查看共享内容，不能改动。
    </div>

    <div class="tabs">
      <button class="tab" :class="{ active: activeTab === 'tasks' }" type="button" @click="switchTab('tasks')">聚合任务</button>
      <button class="tab" :class="{ active: activeTab === 'release' }" type="button" @click="switchTab('release')">放行清单</button>
      <button class="tab" :class="{ active: activeTab === 'review' }" type="button" @click="switchTab('review')">校验规则归属复核清单</button>
    </div>

    <template v-if="activeTab === 'tasks'">
      <div class="stat-row">
        <article v-for="item in stats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>

      <form class="filter-bar" @submit.prevent="reload">
        <label class="filter-item">
          <span>任务编号</span>
          <input v-model="filters.keyword" placeholder="按任务编号检索" />
        </label>
        <label class="filter-item">
          <span>任务状态</span>
          <select v-model="filters.status">
            <option value="">全部</option>
            <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                :disabled="!session.canOperate && action === '生成放行清单'"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 1" class="empty-state">暂无结果聚合归并数据，可先登记聚合任务</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ total }} 条结果聚合归并记录</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
        <span v-else-if="successMessage" class="success-text">{{ successMessage }}</span>
      </footer>
    </template>

    <template v-else-if="activeTab === 'release'">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in releaseColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in releaseRows" :key="String(row.id)">
            <td v-for="column in releaseColumns" :key="column">
              <span v-if="column === '放行明细' || column === '拦截明细'">{{ (row[column] || []).join('、') || '—' }}</span>
              <span v-else>{{ row[column] ?? '—' }}</span>
            </td>
          </tr>
          <tr v-if="!releaseRows.length">
            <td :colspan="releaseColumns.length" class="empty-state">暂无放行清单，请在聚合任务页执行「生成放行清单」</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ releaseTotal }} 条放行清单，可核对规则版本与计划所属团队</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </template>

    <template v-else>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in reviewColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in reviewRows" :key="index">
            <td v-for="column in reviewColumns" :key="column">
              <span v-if="column === '复核状态'" class="badge" :class="reviewBadge(row.复核状态)">{{ row.复核状态 }}</span>
              <span v-else>{{ row[column] ?? '—' }}</span>
            </td>
          </tr>
          <tr v-if="!reviewRows.length">
            <td :colspan="reviewColumns.length" class="empty-state">暂无复核数据，校验放行结果产生后自动归并到这里</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ reviewRows.length }} 条校验规则归属复核记录</span>
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

const ENDPOINT = '/api/merge'
const session = useSessionStore()
const route = useRoute()

const columns = ["任务编号", "采集计划", "分片总数", "已归并数", "归并策略", "输出路径", "数据总量", "任务状态"]
const actions = ["启动归并", "重试归并", "归档任务", "生成放行清单"]
const statuses = ["待归并", "归并中", "已完成", "归并失败"]
const releaseColumns = ["清单编号", "任务编号", "采集计划", "规则编号", "规则版本", "计划所属团队", "审批团队", "交接时间", "原审批撤回依据", "放行明细", "拦截明细", "生成时间"]
const reviewColumns = ["规则编号", "版本号", "采集计划", "计划所属团队", "归属团队", "提交团队", "审批团队", "审批状态", "交接时间", "原审批撤回依据", "放行明细数", "不通过明细数", "复核状态", "最近生成时间"]

const activeTab = ref(['release', 'review'].includes(String(route.query.tab)) ? String(route.query.tab) : 'tasks')
const rows = ref<Row[]>([])
const total = ref(0)
const releaseRows = ref<Row[]>([])
const releaseTotal = ref(0)
const reviewRows = ref<Row[]>([])
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', status: '' })

const stats = computed(() => [
  { label: '聚合任务总数', value: total.value },
  { label: '归并中任务', value: rows.value.filter((row) => row.status === '归并中').length },
  { label: '已完成任务', value: rows.value.filter((row) => row.status === '已完成').length },
  { label: '放行清单', value: releaseTotal.value },
])

function operatorPayload() {
  return { 操作人: session.operator, 操作团队: session.team, 操作角色: session.role }
}

function reviewBadge(status: string) {
  if (status === '归属一致') return 'ok'
  if (status === '待复核') return 'err'
  return 'warn'
}

function switchTab(tab: string) {
  activeTab.value = tab
  if (tab === 'release') void reloadReleaseList()
  if (tab === 'review') void reloadReview()
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '聚合任务登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, ...operatorPayload() } }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '结果聚合归并动作未生效，请稍后重试')
    successMessage.value = payload.message
    await reload()
    if (action === '生成放行清单') {
      await reloadReleaseList()
      await reloadReview()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结果聚合归并操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.status) query.set('status', filters.value.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('聚合任务列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结果聚合归并列表读取失败'
  }
}

async function reloadReleaseList() {
  try {
    const response = await request(`${ENDPOINT}/release-list`)
    if (!response.ok) throw new Error('放行清单读取失败')
    const payload = await response.json()
    releaseRows.value = payload.items ?? []
    releaseTotal.value = payload.total ?? releaseRows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '放行清单读取失败'
  }
}

async function reloadReview() {
  try {
    const response = await request(`${ENDPOINT}/verify-review`)
    if (!response.ok) throw new Error('校验规则归属复核清单读取失败')
    const payload = await response.json()
    reviewRows.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '校验规则归属复核清单读取失败'
  }
}

watch(() => route.query.tab, (tab) => {
  const name = String(tab ?? 'tasks')
  activeTab.value = ['release', 'review'].includes(name) ? name : 'tasks'
  if (name === 'release') void reloadReleaseList()
  if (name === 'review') void reloadReview()
})

onMounted(async () => {
  await Promise.all([reload(), reloadReleaseList(), reloadReview()])
})
</script>
