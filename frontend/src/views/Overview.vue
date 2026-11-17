<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <div class="link-row">
      <RouterLink class="link-card" to="/verify?tab=release">
        <span class="stat-label">校验放行结果数据（采集结果校验）</span>
        <strong class="stat-value">{{ linkage.releaseTotal }}</strong>
      </RouterLink>
      <RouterLink class="link-card" to="/merge?tab=review">
        <span class="stat-label">校验规则归属复核清单（归并下游）</span>
        <strong class="stat-value">{{ linkage.reviewTotal }}</strong>
      </RouterLink>
      <RouterLink class="link-card" to="/merge?tab=release">
        <span class="stat-label">结果聚合放行清单（归并下游）</span>
        <strong class="stat-value">{{ linkage.mergeReleaseTotal }}</strong>
      </RouterLink>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const linkage = ref({ releaseTotal: 0, reviewTotal: 0, mergeReleaseTotal: 0 })

async function reloadLinkage() {
  try {
    const [release, review, mergeRelease] = await Promise.all([
      fetchJson<{ total: number }>('/api/verify/release?size=1'),
      fetchJson<{ total: number }>('/api/merge/verify-review'),
      fetchJson<{ total: number }>('/api/merge/release-list?size=1'),
    ])
    linkage.value = {
      releaseTotal: release.total ?? 0,
      reviewTotal: review.total ?? 0,
      mergeReleaseTotal: mergeRelease.total ?? 0,
    }
  } catch {
    linkage.value = { releaseTotal: 0, reviewTotal: 0, mergeReleaseTotal: 0 }
  }
}

onMounted(async () => {
  void reloadLinkage()
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "采集节点集群", "created": 0, "pending": 0, "abnormal": 0}, {"name": "任务分片调度", "created": 0, "pending": 0, "abnormal": 0}, {"name": "结果聚合归并", "created": 0, "pending": 0, "abnormal": 0}, {"name": "节点状态巡检", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集流量均衡", "created": 0, "pending": 0, "abnormal": 0}, {"name": "断点续采恢复", "created": 0, "pending": 0, "abnormal": 0}, {"name": "全局采集看板", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集配置模板", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集数据源池", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集日志解析", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集频率控制", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集代理路由", "created": 0, "pending": 0, "abnormal": 0}, {"name": "RPA执行记录", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集结果校验", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集操作审计", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集存储分层", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>
