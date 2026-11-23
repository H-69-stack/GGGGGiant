import { defineStore } from 'pinia'
import { ppApi } from '@/api/pp'

export const useRfmStore = defineStore('rfm', {
  state: () => ({
    /** PP design session ID (e.g. "db_17") */
    sessionId: sessionStorage.getItem('rfm_session_id') || '',
    /** Active PP design record from pp_design.db */
    designRecord: null,
    /** Display name */
    displayName: '',
    /** Centerline length (mm) */
    centerlineLength: 0,
    /** Number of sections */
    sectionsCount: 0,
    /**
     * Wall thickness data: [{ 中心线长度(mm), 外径周长(mm), 壁厚(mm) }, ...]
     * Also contains x_length / y_length if available.
     */
    wallThicknessData: [],
    /** Full section data with positions */
    sectionsFull: [],
    /**
     * Process design params (L1, T3, D1, D2, D3, T1..T4, TL1..TL5, etc.)
     * These come from pp_design.db params_json.
     */
    designParams: {},
    processConfigs: {},
    /**
     * Tube number from design record
     */
    tubeNumber: '',
    /**
     * Vehicle spec
     */
    vehicleSpec: '',
    /**
     * PP method (抽管方式)
     */
    ppMethod: '',
  }),

  getters: {
    hasSession: (state) => !!state.sessionId && !!state.designRecord,
    /** L1: 扩口后保留长度 (mm) — used by Belling */
    L1: (state) => parseFloat(state.designParams['L1'] || state.designParams['L1'] || 0),
    /** T3: 壁厚3 — used by Belling */
    T3: (state) => parseFloat(state.designParams['T3'] || 0),
    /** D1: 扩口段大径 — used by Belling */
    D1: (state) => parseFloat(state.designParams['D1'] || 0),
    /** D2: TP抽段小径 */
    D2: (state) => parseFloat(state.designParams['D2'] || 0),
    /** D3: Taper小径/直线段直径 */
    D3: (state) => parseFloat(state.designParams['D3'] || 0),
    /** T1, T2, T3, T4 */
    T1: (state) => parseFloat(state.designParams['T1'] || 0),
    T2: (state) => parseFloat(state.designParams['T2'] || 0),
    T4: (state) => parseFloat(state.designParams['T4'] || 0),
    /** TL1..TL5: 每段壁厚对应长度 */
    TL1: (state) => parseFloat(state.designParams['TL1'] || 0),
    TL2: (state) => parseFloat(state.designParams['TL2'] || 0),
    TL3: (state) => parseFloat(state.designParams['TL3'] || 0),
    TL4: (state) => parseFloat(state.designParams['TL4'] || 0),
    /** L4: 直线段长度 */
    L4: (state) => parseFloat(state.designParams['L4'] || 0),
    /** Ln: 普通抽长度 */
    Ln: (state) => parseFloat(state.designParams['Ln'] || 0),
    /** LL: 总长度 */
    LL: (state) => parseFloat(state.designParams['LL'] || 0),
    /** BL: 弯管长度 */
    BL: (state) => parseFloat(state.designParams['BL'] || 0),
    /** B_D: 弯管直径 */
    B_D: (state) => parseFloat(state.designParams['B_D'] || 0),
  },

  actions: {
    async loadDesign(designId) {
      try {
        const res = await ppApi.getDesign(designId)
        if (!res.data.success) {
          throw new Error(res.data.message || '加载失败')
        }
        const rec = res.data.design
        this.designRecord = rec
        this.displayName = rec.display_name || rec.tube_number || String(designId)
        this.tubeNumber = rec.tube_number || ''
        this.vehicleSpec = rec.vehicle_spec || ''
        this.ppMethod = rec.pp_method || ''

        // Design params (L1, T3, D1, etc.)
        this.designParams = rec.params || {}
        try {
          const saved = JSON.parse(sessionStorage.getItem('rfm_process_configs') || '{}')
          this.processConfigs = saved.designId === rec.id ? saved.configs || {} : {}
        } catch { this.processConfigs = {} }

        // Centerline perimeter data
        const cp = rec.centerline_perimeter_data || []
        this.wallThicknessData = cp

        // Source tube data (from raw_payload)
        const raw = rec.raw_payload || {}
        const results = raw.results || {}

        const formlineSpan = parseFloat(cp.at(-1)?.['中心线长度(mm)'] || 0)
        const drawingLL = parseFloat(this.designParams.LL || 0)
        this.centerlineLength = formlineSpan || drawingLL || parseFloat(results.centerline_length || results.center_line_length || 0)
        const sectionsFull = results.sections_full || []
        this.sectionsCount = sectionsFull.length
        this.sectionsFull = sectionsFull

        // Session ID = design record id for RFM
        this.sessionId = `design_${designId}`
        sessionStorage.setItem('rfm_session_id', this.sessionId)

        return rec
      } catch (e) {
        console.error('[RfmStore] loadDesign error:', e)
        throw e
      }
    },

    rememberProcessConfig(operation, config) {
      this.processConfigs[operation] = { ...config }
      sessionStorage.setItem('rfm_process_configs', JSON.stringify({ designId: this.designRecord?.id, configs: this.processConfigs }))
    },

    clearSession() {
      this.sessionId = ''
      this.designRecord = null
      this.displayName = ''
      this.centerlineLength = 0
      this.sectionsCount = 0
      this.wallThicknessData = []
      this.sectionsFull = []
      this.designParams = {}
      this.processConfigs = {}
      this.tubeNumber = ''
      this.vehicleSpec = ''
      this.ppMethod = ''
      sessionStorage.removeItem('rfm_session_id')
    },
  },
})
