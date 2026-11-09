<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import client from '../api/client'
import { getInterviewTargets, getInterviewerOptions, startInterview, type InterviewTarget } from '../api/interview'
import { trainingBoard, type TrainingBoard, type TrainingMode } from '../api/training'
import type { InterviewerOptions } from '../types/interview'
import { capabilityLabels, capabilityReasons } from '../types/capability-memory'
import { publicName } from '../i18n/zh-CN'
const route=useRoute(), router=useRouter()
const jobs=ref<InterviewTarget[]>([]), options=ref<InterviewerOptions|null>(null)
const jobId=ref(''), skill=ref(''), round=ref('technical_screen'), style=ref('standard'), count=ref(4)
const mode=ref<TrainingMode>('FULL'), goal=ref(''), board=ref<TrainingBoard|null>(null)
const loading=ref(false), stage=ref(''), error=ref(''), saved=ref<number|null>(null)
const manualSkill=ref(false)
let sequence=0
const job=computed(()=>jobs.value.find(j=>String(j.job_id)===jobId.value))
const available=computed(()=>board.value?.targets.filter(t=>t.available_modes.includes(mode.value))||[])
function selectTarget(id:string, selected:TrainingMode){mode.value=selected;goal.value=id}
const reason=(tags:string[])=>tags.map(t=>capabilityReasons[t]||'结合新的岗位场景继续验证').join('；')
function request(){return {company_id:job.value?.company_id,job_id:job.value?.job_id,interviewer_skill_id:skill.value,
  adaptive_interview:true,interview_round:round.value,interviewer_style:style.value,question_count:count.value}}
async function load(){
  const current=++sequence; board.value=null; error.value=''
  if(!job.value||!skill.value){loading.value=false;return}
  loading.value=true
  try { const result=await trainingBoard(request()); if(current===sequence)board.value=result }
  catch(e){if(current===sequence)error.value=e instanceof Error?e.message:'训练目标读取失败，请重试'}
  finally{if(current===sequence)loading.value=false}
}
function chooseJob(){
  if(!manualSkill.value)skill.value=job.value?.interviewer_skill_recommendation?.recommended_skill_id||''
  goal.value=''; void load()
}
async function begin(){
  error.value=''; saved.value=null
  if(!job.value||!skill.value||!board.value?.targets.length||(mode.value!=='FULL'&&!goal.value))return
  const body={...request(),training_mode:mode.value,training_knowledge_id:mode.value==='FULL'?null:goal.value}
  const signature=JSON.stringify(body)
  let pending: {signature:string;key:string}|null=null
  try{pending=JSON.parse(sessionStorage.getItem('training_pending')||'null')}catch{ /* malformed local draft is ignored */ }
  if(!pending||pending.signature!==signature){pending={signature,key:crypto.randomUUID()};sessionStorage.setItem('training_pending',JSON.stringify(pending))}
  stage.value='正在创建并固定训练目标…'
  try{
    const created=await client.post('/interview/sessions',{...body,training_request_key:pending.key}) as unknown as {id:number}
    saved.value=created.id; stage.value='正在准备第一道问题…'
    await startInterview(created.id)
    sessionStorage.removeItem('training_pending')
    await router.push({path:'/interview',query:{session_id:created.id}})
  }catch(e){error.value=e instanceof Error?e.message:'训练创建失败，请安全重试'}
  finally{stage.value=''}
}
watch([skill,round,style],()=>{void load()})
watch(mode,()=>{if(!available.value.some(t=>t.knowledge_id===goal.value))goal.value=''; if(mode.value==='RECHECK'&&style.value==='gentle')style.value='standard'})
onMounted(async()=>{
  loading.value=true
  try{
    const [targets,catalog]=await Promise.all([getInterviewTargets(),getInterviewerOptions()]);jobs.value=targets;options.value=catalog
    jobId.value=String(route.query.job_id||targets[0]?.job_id||'')
    skill.value=String(route.query.recommended_skill_id||job.value?.interviewer_skill_recommendation?.recommended_skill_id||'')
    await load()
    const intended=String(route.query.mode||'FULL')
    if(['FULL','WEAKNESS','RECHECK'].includes(intended))mode.value=intended as TrainingMode
    const wanted=String(route.query.knowledge_id||'')
    if(available.value.some(t=>t.knowledge_id===wanted))goal.value=wanted
  }catch(e){error.value=e instanceof Error?e.message:'训练入口读取失败'}finally{loading.value=false}
})
</script>

<template>
  <main class="training-page">
    <header><h1>个性化面试训练</h1><p>选定目标岗位，先看哪些知识需要练习，再决定如何开始。</p></header>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重试读取</button></p>
    <section class="setup" aria-label="训练配置">
      <label>目标岗位<select v-model="jobId" @change="chooseJob"><option value="">请选择已发布岗位</option><option v-for="j in jobs" :key="j.job_id" :value="String(j.job_id)">{{ publicName(j.company_name) }} · {{ publicName(j.job_title) }}</option></select></label>
      <label>专业方向<select v-model="skill" @change="manualSkill=true"><option value="">请选择专业 Skill</option><option v-for="s in options?.skills" :key="s.id" :value="s.id">{{ s.label }}</option></select></label>
      <label>面试轮次<select v-model="round"><option v-for="r in options?.rounds" :key="r.id" :value="r.id">{{ r.label }}</option></select></label>
      <label>面试官风格<select v-model="style"><option v-for="s in options?.styles" :key="s.id" :value="s.id">{{ s.label }}</option></select></label>
    </section>
    <p v-if="loading" role="status">正在核对岗位范围和有效历史表现…</p>
    <section v-if="board" class="targets"><h2>这个岗位适合练什么</h2>
      <p v-if="!board.targets.length">当前岗位与专业规则没有合法考察目标，请更换专业方向，暂不创建训练任务。</p>
      <p v-if="!board.coverage.reviewed_question_count">该岗位暂无已审核的专属面试题，使用合法专业规则和岗位要求出题，不冒充公司历史真题。</p>
      <ul><li v-for="t in board.targets" :key="t.knowledge_id"><div><strong>{{ t.label }}</strong><span>{{ t.revoked?'已撤销推断':capabilityLabels[t.state] }}</span></div>
        <p>{{ reason(t.reason_tags)||'尚无足够历史观察，可从完整模拟面试开始。' }} <span v-if="t.independent_sessions">独立支持 {{t.independent_sessions}} 场。</span></p>
        <button v-if="t.available_modes.includes('RECHECK')" @click="selectTarget(t.knowledge_id,'RECHECK')">选择此目标独立复测</button>
        <button v-else-if="t.available_modes.includes('WEAKNESS')" @click="selectTarget(t.knowledge_id,'WEAKNESS')">选择此弱项练习</button>
        <details v-if="t.observations.length"><summary>为什么推荐 / 查看历史依据</summary><p v-for="o in t.observations" :key="o.session_id+o.observed_at">{{ o.assisted?'提示或追问后作答':'独立作答' }} <router-link :to="`/interview?session_id=${o.session_id}`">查看来源面试</router-link></p><router-link to="/memory">更正或撤销有争议的推断</router-link></details>
      </li></ul>
    </section>
    <section v-if="board" class="launch"><h2>选择本次练习方式</h2>
      <label>训练模式<select v-model="mode"><option value="FULL">完整模拟面试</option><option value="WEAKNESS">薄弱项专项训练</option><option value="RECHECK">冲突 / 遗忘独立复测</option></select></label>
      <label v-if="mode!=='FULL'">训练知识点<select v-model="goal"><option value="">请选择本次目标</option><option v-for="t in available" :key="t.knowledge_id" :value="t.knowledge_id">{{t.label}} · {{capabilityLabels[t.state]}}</option></select></label>
      <p v-if="mode!=='FULL'&&!available.length">当前没有符合此模式的有效知识目标。可选择完整模拟面试，不会凭空生成复测任务。</p>
      <p v-if="mode==='RECHECK'">第一题先独立作答，不提前提供标准答案；之后的提示与追问不算独立验证。请使用标准或严格风格。</p>
      <label>本次轮数（含追问）<input v-model.number="count" type="number" min="1" max="12" /></label>
      <button class="primary" :disabled="loading||!!stage||!board.targets.length||(mode!=='FULL'&&!goal)" @click="begin">{{stage||'开始本次训练'}}</button>
      <p v-if="stage" role="status">{{stage}}</p>
      <router-link v-if="saved&&error" :to="`/interview?session_id=${saved}`">查看已创建的训练并安全恢复</router-link>
    </section>
    <p v-if="!loading&&!jobs.length">当前没有可用的已发布岗位，请等待管理员审核资料。</p>
    <router-link to="/memory">查看全部成长记录</router-link>
  </main>
</template>

<style scoped>
.training-page{max-width:1000px;margin:0 auto;padding:24px;color:#20242a}h1{font-size:28px;margin-bottom:8px}h2{font-size:20px}p{max-width:76ch;color:#667085;line-height:1.7}section{margin:24px 0;padding:22px;background:white;border:1px solid #e5e7eb;border-radius:8px}.setup{display:grid;grid-template-columns:1fr 1fr;gap:16px}label{display:block;margin-bottom:12px}select,input{display:block;box-sizing:border-box;width:100%;padding:10px;margin-top:6px;border:1px solid #d0d5dd;border-radius:6px;background:white}.targets ul{padding:0;list-style:none}.targets li{padding:16px 0;border-bottom:1px solid #eef2f6}.targets li div{display:flex;justify-content:space-between;gap:12px}.targets span{color:#667085}summary{cursor:pointer;color:#245f55}button{padding:10px 18px;border:1px solid #d0d5dd;border-radius:6px;cursor:pointer}.primary{background:#245f55;color:white}button:disabled{opacity:.55;cursor:default}[role=alert]{color:#b42318}.launch{border-left:4px solid #245f55}a{color:#245f55}:focus-visible{outline:2px solid #245f55;outline-offset:3px}@media(max-width:640px){.setup{grid-template-columns:1fr}.training-page{padding:16px}}
</style>
