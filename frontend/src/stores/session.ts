import { defineStore } from 'pinia'

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    team: '采集一组',
    role: '操作员',
    teams: ['采集一组', '采集二组', '采集三组'],
    roles: ['操作员', '管理员', '只读'],
    shiftLabel: '白班 08:00-20:00',
    scope: '分布式采集调度引擎',
  }),
  getters: {
    canOperate: (state) => state.role !== '只读',
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setTeam(team: string) {
      this.team = team
    },
    setRole(role: string) {
      this.role = role
    },
  },
})
