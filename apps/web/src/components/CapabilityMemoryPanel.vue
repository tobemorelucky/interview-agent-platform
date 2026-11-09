<script setup lang="ts">
import { onMounted, ref } from "vue"
import { listCapabilities, correctCapability } from "../api/memory"
import { capabilityLabels, capabilityReasons, type CapabilityMemory } from "../types/capability-memory"
const items = ref<CapabilityMemory[]>([])
const loading = ref(false), error = ref(""), saving = ref(false)
const editing = ref<number | null>(null), note = ref(""), excluded = ref<number[]>([])
const date = (value: string | null) => value ? new Date(value).toLocaleDateString("zh-CN") : "尚无充分独立验证"
const time = (value: string) => new Date(value).toLocaleString("zh-CN")
async function load() {
  loading.value = true
  try { items.value = (await listCapabilities()).items; error.value = "" }
  catch (e) { error.value = e instanceof Error ? e.message : "能力记忆读取失败，请重试" }
  finally { loading.value = false }
}
function edit(item: CapabilityMemory) { editing.value = item.id; note.value = ""; excluded.value = [] }
async function save(item: CapabilityMemory, revoke = false) {
  if (!note.value.trim()) { error.value = "请说明更正或撤销原因"; return }
  saving.value = true
  try {
    await correctCapability(item.id, { expected_revision: item.revision, excluded_evaluation_ids: excluded.value, note: note.value, revoke })
    editing.value = null; await load()
  } catch (e) { error.value = e instanceof Error ? e.message : "更正失败，请刷新后重试" }
  finally { saving.value = false }
}
onMounted(load)
</script>

<template>
  <section class="capability-panel">
    <header><div><h3>能力记忆</h3><p>根据多场面试表现推断，不是本人确认的经历，也不是技能认证。下一场仅参考相关知识点，不改变评分。</p></div><button @click="load" :disabled="loading">刷新</button></header>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在读取能力变化…</p>
    <p v-else-if="!items.length">完成一场自适应面试后，这里会记录知识点表现。没有记录不表示你不会。</p>
    <article v-for="item in items" :key="item.id">
      <div class="title"><h4>{{ item.label }}</h4><span>{{ item.revoked ? '已撤销推断' : capabilityLabels[item.state] }}</span></div>
      <p>上次充分验证：{{ date(item.last_verified_at) }} · 独立支持 {{ item.independent_sessions }} 场</p>
      <ul><li v-for="reason in item.reason_tags" :key="reason">{{ capabilityReasons[reason] || '建议结合新场景继续核对' }}</li></ul>
      <p v-if="item.conflicts.some(c => c.status === 'OPEN')">尚有未解决分歧，下一场建议独立复测，不会直接覆盖历史结论。</p>
      <p v-if="item.scope_differences.length">不同岗位或难度下的表现不同，暂不归为同一条件下的冲突。</p>
      <p v-if="item.state === 'VERIFIED' && !item.revoked">近期已有独立验证，下一场会降低重复考察优先级；岗位特别需要时仍可核对。</p>
      <p v-if="!item.revoked && ['GAP_SUSPECTED', 'PRACTICING', 'CONFLICTED', 'RECHECK_DUE'].includes(item.state)">下一场为什么考察：岗位相关时，面试官可用新场景核对上述机制；不会强制重复已验证内容。</p>
      <router-link v-if="!item.revoked" :to="{path:'/training',query:{mode:['CONFLICTED','RECHECK_DUE'].includes(item.state)?'RECHECK':['GAP_SUSPECTED','PRACTICING'].includes(item.state)?'WEAKNESS':'FULL'}}">选择岗位并发起训练 / 复测</router-link>
      <p v-if="item.correction_note">本人更正：{{ item.correction_note }}</p>
      <details><summary>查看表现变化与依据</summary>
        <ol><li v-for="row in item.observations" :key="row.evaluation_id">{{ time(row.observed_at) }} · {{ row.assisted ? '提示或追问后作答' : '独立作答' }} · {{ row.state === 'DEMONSTRATED_IN_SESSION' ? '本轮已有支持' : '本轮仍需验证' }}<span v-if="row.open_gaps.length">：{{ row.open_gaps.join('；') }}</span> <router-link :to="`/interview?session_id=${row.session_id}`">查看本次面试</router-link></li></ol>
        <p v-for="row in item.history" :key="row.revision">{{ date(row.created_at) }} · {{ capabilityLabels[row.previous_state] }} → {{ capabilityLabels[row.state] }}{{ row.actor === 'USER' ? '（本人更正）' : '' }}</p>
      </details>
      <button v-if="editing !== item.id && !item.revoked" @click="edit(item)">更正 / 撤销推断</button>
      <form v-if="editing === item.id" @submit.prevent="save(item)">
        <p>可排除有争议的评价，历史原记录保留；不会把更正自动当作能力证明。</p>
        <label v-for="(row, index) in item.observations" :key="row.evaluation_id"><input type="checkbox" v-model="excluded" :value="row.evaluation_id" />排除第 {{ index + 1 }} 次观察（{{ time(row.observed_at) }} · {{ row.state === 'DEMONSTRATED_IN_SESSION' ? '已有支持' : '仍需验证' }}）</label>
        <textarea v-model="note" aria-label="更正原因" maxlength="500" placeholder="说明更正或撤销原因" required />
        <button :disabled="saving" type="submit">保存更正</button>
        <button :disabled="saving" type="button" @click="save(item, true)">撤销此知识点推断</button>
        <button type="button" @click="editing = null">取消</button>
      </form>
    </article>
  </section>
</template>

<style scoped>
.capability-panel{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:18px;margin-bottom:18px;color:#20242a}
header,.title{display:flex;justify-content:space-between;align-items:center;gap:16px}h3,h4{margin:0 0 8px}p,li{color:#667085;line-height:1.65;max-width:80ch}article{border-top:1px solid #eef2f6;padding:18px 0}.title span{background:#f2f4f7;color:#475467;padding:4px 10px;border-radius:16px;font-size:13px}button{padding:7px 12px;border:1px solid #d0d5dd;background:#fff;border-radius:6px;color:#245f55;cursor:pointer}details{margin:12px 0}label{display:block;margin:8px 0}textarea{display:block;width:100%;max-width:600px;min-height:70px;box-sizing:border-box;margin:12px 0}form button{margin-right:8px}button:disabled{opacity:.5}summary{cursor:pointer;color:#245f55}
</style>
