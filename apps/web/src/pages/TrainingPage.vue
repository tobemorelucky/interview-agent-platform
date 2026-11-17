<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import client from '../api/client'
import { getInterviewTargets, getInterviewerOptions, getSession, startInterview, type InterviewTarget } from '../api/interview'
import { trainingBoard, trainingCandidates, type TrainingBoard, type TrainingMode } from '../api/training'
import { candidateEmptyLabels, candidateModeLabels, type TrainingCandidate, type TrainingCandidateSet } from '../types/training-candidates'
import { listTargets } from '../api/job-target'
import type { JobTarget } from '../types/job-target'
import type { InterviewerOptions } from '../types/interview'
import { capabilityLabels, capabilityReasons } from '../types/capability-memory'
import { publicName } from '../i18n/zh-CN'
const route=useRoute(), router=useRouter()
const jobs=ref<InterviewTarget[]>([]), options=ref<InterviewerOptions|null>(null)
const privateTargets=ref<JobTarget[]>([]), privateTargetId=ref('')
const jobId=ref(''), skill=ref(''), round=ref('technical_screen'), style=ref('standard'), count=ref(4)
const mode=ref<TrainingMode>('WEAKNESS'), goal=ref(''), board=ref<TrainingBoard|null>(null)
const candidates=ref<TrainingCandidateSet|null>(null), candidateError=ref(''), notice=ref('')
const loading=ref(false), stage=ref(''), error=ref(''), saved=ref<number|null>(null)
const needsProfile=ref(false)
const manualSkill=ref(false)
// Only the rule's clear winner from the JD page may be re-validated as AUTO.
const autoRecommended=ref(route.query.recommendation_source==='AUTO_RECOMMENDED')
let sequence=0, candidateSequence=0
const job=computed(()=>jobs.value.find(j=>String(j.job_id)===jobId.value))
const privateTarget=computed(()=>privateTargets.value.find(t=>String(t.id)===privateTargetId.value))
const hasSource=computed(()=>!!(privateTargetId.value||jobId.value))
const available=computed(()=>board.value?.targets.filter(t=>t.available_modes.includes(mode.value))||[])
function selectTarget(id:string, selected:TrainingMode){mode.value=selected;goal.value=id}
// Only goals the rules actually allow a专项 run for; FULL belongs to /interview.
const trainable=computed(()=>(board.value?.targets||[]).filter(t=>t.available_modes.some(m=>m==='WEAKNESS'||m==='RECHECK')))
const plainMode=(m:string)=>m==='RECHECK'?'独立复测':m==='WEAKNESS'?'薄弱项练习':'完整面试'
const reason=(tags:string[])=>tags.map(t=>capabilityReasons[t]||'结合新的岗位场景继续验证').join('；')
function request(){
  // Exactly one job source; the server re-checks ownership for private ids.
  const source=privateTargetId.value
    ? {job_target_id:Number(privateTargetId.value)}
    : {company_id:job.value?.company_id,job_id:job.value?.job_id}
  return {...source,interviewer_skill_id:skill.value,
    adaptive_interview:true,interview_round:round.value,interviewer_style:style.value,question_count:count.value,
    interviewer_selection_source:autoRecommended.value?'AUTO_RECOMMENDED':'USER_SELECTED'}
}
async function load(){
  const current=++sequence; board.value=null; candidates.value=null
  error.value=''; candidateError.value=''; notice.value=''; needsProfile.value=false
  if(!hasSource.value||!skill.value){loading.value=false;return}
  loading.value=true
  try { const result=await trainingBoard(request()); if(current===sequence)board.value=result }
  catch(e){if(current===sequence){
    error.value=e instanceof Error?e.message:'训练目标读取失败，请重试'
    // A missing profile is actionable: point at the resume upload instead of
    // leaving the user on a page that can never load.
    needsProfile.value=(e as {code?:string}).code==='PROFILE_REQUIRED'
  }}
  finally{if(current===sequence)loading.value=false}
  void loadCandidates(current)
}
async function loadCandidates(current:number){
  const seq=++candidateSequence
  try{
    const result=await trainingCandidates(request())
    if(seq===candidateSequence&&current===sequence)candidates.value=result
  }catch(e){if(seq===candidateSequence&&current===sequence){
    candidates.value=null
    candidateError.value=e instanceof Error?e.message:'训练候选读取失败'
  }}
}
function onSkillChange(){manualSkill.value=true; autoRecommended.value=false}
async function selectCandidate(c:TrainingCandidate){
  // Preselect only. The creation button still re-runs every server-side check.
  if(c.skill_id)skill.value=c.skill_id
  if(!hasSource.value){
    if(c.launch.query.job_target_id)privateTargetId.value=c.launch.query.job_target_id
    else if(c.launch.query.job_id)jobId.value=c.launch.query.job_id
  }
  mode.value=c.mode as TrainingMode
  await nextTick() // let the mode watcher clear a stale goal first
  goal.value=c.knowledge_id
  notice.value=`已预选「${c.label}」· ${candidateModeLabels[c.mode]}；请确认岗位、专业方向与知识点后，再点击「开始本次训练」。本次跳转没有创建任何训练会话。`
}
function chooseJob(){
  if(jobId.value)privateTargetId.value=''
  if(!manualSkill.value)skill.value=job.value?.interviewer_skill_recommendation?.recommended_skill_id||''
  goal.value=''; void load()
}
function choosePrivateTarget(){
  if(privateTargetId.value)jobId.value=''
  // A private JD has no public job to inherit a Skill from; the user picks one.
  goal.value=''; void load()
}
watch(privateTargetId,choosePrivateTarget)
watch(jobId,chooseJob)
async function begin(){
  error.value=''; saved.value=null
  if(!hasSource.value||!skill.value||!board.value?.targets.length||(mode.value!=='FULL'&&!goal.value))return
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
    const [targets,catalog,privateList]=await Promise.all([
      getInterviewTargets(),getInterviewerOptions(),listTargets().catch(()=>[] as JobTarget[])
    ])
    jobs.value=targets; options.value=catalog
    privateTargets.value=privateList.filter(t=>t.parse_status==='CONFIRMED')
    // From a report: read the frozen private target and Skill from that session,
    // never from a mutable JD that may have been edited afterwards.
    let privateId=String(route.query.job_target_id||''), frozenSkill=''
    const sessionId=Number(route.query.session_id||0)
    if(!privateId&&sessionId){
      const detail=await getSession(sessionId) as unknown as {context_snapshot?:{
        job_target?:{id?:number};interviewer_config?:{skill?:{id?:string}}}}
      privateId=String(detail.context_snapshot?.job_target?.id||'')
      frozenSkill=detail.context_snapshot?.interviewer_config?.skill?.id||''
    }
    manualSkill.value=!!(route.query.recommended_skill_id||frozenSkill)
    if(privateId&&privateTargets.value.some(t=>String(t.id)===privateId))privateTargetId.value=privateId
    else jobId.value=String(route.query.job_id||targets[0]?.job_id||'')
    skill.value=String(route.query.recommended_skill_id||frozenSkill||
      (privateTargetId.value?'':job.value?.interviewer_skill_recommendation?.recommended_skill_id||''))
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
    <header class="page-head">
      <div>
        <h1>专项训练</h1>
        <p>这里根据你上一场模拟面试的表现，挑出值得单独练的知识点。需要完整模拟面试，请到
          <router-link to="/interview">模拟面试</router-link>。</p>
      </div>
    </header>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重试读取</button>
      <router-link v-if="needsProfile" to="/resumes">去上传简历</router-link></p>
    <section class="setup" aria-label="训练配置">
      <label>目标岗位<select v-model="jobId"><option value="">请选择已发布岗位</option><option v-for="j in jobs" :key="j.job_id" :value="String(j.job_id)">{{ publicName(j.company_name) }} · {{ publicName(j.job_title) }}</option></select></label>
      <label v-if="privateTargets.length">我的岗位（自己提供的 JD）<select v-model="privateTargetId"><option value="">不使用私人岗位</option><option v-for="t in privateTargets" :key="t.id" :value="String(t.id)">{{ t.company_name }} · {{ t.job_title }} （第 {{ t.version }} 版）</option></select></label>
      <label>专业方向<select v-model="skill" @change="onSkillChange"><option value="">请选择专业 Skill</option><option v-for="s in options?.skills" :key="s.id" :value="s.id">{{ s.label }}</option></select></label>
      <label>面试轮次<select v-model="round"><option v-for="r in options?.rounds" :key="r.id" :value="r.id">{{ r.label }}</option></select></label>
      <label>面试官风格<select v-model="style"><option v-for="s in options?.styles" :key="s.id" :value="s.id">{{ s.label }}</option></select></label>
    </section>
    <p v-if="privateTarget" class="provenance" role="note">当前目标来自你自己提供的 JD（{{ privateTarget.company_name }} · {{ privateTarget.job_title }}，第 {{ privateTarget.version }} 版），{{ privateTarget.source_label }}。训练只使用该目标已确认的岗位要求，以及你在上方选择的平台专业 Skill；它不会进入公开招聘岗位或公共题库。</p>
    <p v-if="privateTarget && !skill" role="note">请先在上方「专业方向」里选择一个已注册 Skill；平台只会在你选定的 Skill 与你确认的岗位要求范围内生成训练目标。</p>
    <p v-if="loading" role="status">正在核对岗位范围和有效历史表现…</p>
    <section v-if="board" class="targets"><h2>可以练什么</h2>
      <p v-if="!trainable.length" class="empty-state">
        还没有可以专项练习的目标。先完成一场<router-link to="/interview">完整的模拟面试</router-link>，
        系统会根据那一场的正式评价判断哪些点值得单独练；在既有规则下，不会凭空生成弱项。
      </p>
      <p v-if="!board.coverage.reviewed_question_count" class="meta">该岗位暂无已审核的专属面试题，使用专业规则和岗位要求出题，不冒充公司真题。</p>
      <ul><li v-for="t in trainable" :key="t.knowledge_id"><div><strong>{{ t.label }}</strong><span>{{ capabilityLabels[t.state] }}</span></div>
        <p>{{ reason(t.reason_tags)||'尚无足够历史观察。' }} <span v-if="t.independent_sessions">已在 {{ t.independent_sessions }} 场面试中被独立验证过。</span></p>
        <button v-if="t.available_modes.includes('RECHECK')" @click="selectTarget(t.knowledge_id,'RECHECK')">安排独立复测</button>
        <button v-else @click="selectTarget(t.knowledge_id,'WEAKNESS')">练这个薄弱项</button>
        <details v-if="t.observations.length"><summary>这个判断来自哪场面试</summary>
          <p v-for="o in t.observations" :key="o.session_id+o.observed_at">
            {{ o.assisted?'提示或追问后作答':'独立作答' }}
            <router-link :to="`/interview?session_id=${o.session_id}`">查看来源面试</router-link>
          </p>
          <router-link to="/memory">认为判断有误？去更正或撤销</router-link>
        </details>
      </li></ul>
    </section>
    <details v-if="candidates" class="candidate-panel advanced">
      <summary>系统推荐的练习顺序（内部依据）</summary>
      <p class="meta">{{ candidates.semantics }}</p>
      <p class="meta">{{ candidates.disclaimer }}</p>
      <p v-if="candidateError" role="alert">候选读取失败：{{ candidateError }}</p>
      <p v-if="candidates.status==='TARGET_UNAVAILABLE'" role="alert">{{ candidates.message || candidateEmptyLabels[candidates.empty_reason||''] }}</p>
      <p v-else-if="!candidates.candidates.length" role="status">{{ candidateEmptyLabels[candidates.empty_reason||''] || '当前没有可训练候选。' }}</p>
      <ul><li v-for="c in candidates.candidates" :key="`${c.knowledge_id}-${c.mode}`">
        <div><strong>{{ c.label }}</strong><span>{{ plainMode(c.mode) }} · {{ capabilityLabels[c.state]||c.state }}</span></div>
        <p>{{ c.reason }}</p>
        <p class="meta">依据：{{ c.evidence.evaluation_refs.length ? c.evidence.evaluation_refs.join('、') : '暂无正式评价引用' }}
          · 独立会话 {{ c.evidence.independent_sessions }}</p>
        <button :aria-label="`选用 ${c.label} 候选`" @click="selectCandidate(c)">预选这个目标</button>
      </li></ul>
      <p v-if="candidates.fallback" class="meta">{{ candidates.fallback.reason }}</p>
    </details>
    <p v-if="notice" role="status" class="candidate-notice">{{ notice }}</p>
    <section v-if="board && trainable.length" class="launch"><h2>开始这次专项练习</h2>
      <label>练哪个知识点<select v-model="goal"><option value="">请选择本次目标</option><option v-for="t in trainable.filter(t=>t.available_modes.includes(mode))" :key="t.knowledge_id" :value="t.knowledge_id">{{t.label}} · {{capabilityLabels[t.state]}}</option></select></label>
      <p class="meta" v-if="goal">练习方式：{{ plainMode(mode) }}<template v-if="mode==='RECHECK'">，第一题先独立作答，不提前给答案；之后的提示不算独立验证。</template></p>
      <label>本次题量<input v-model.number="count" type="number" min="1" max="12" /></label>
      <button class="primary" :disabled="loading||!!stage||!goal" @click="begin">{{stage||'开始专项练习'}}</button>
      <p v-if="stage" role="status">{{stage}}</p>
      <router-link v-if="saved&&error" :to="`/interview?session_id=${saved}`">查看已创建的训练并安全恢复</router-link>
    </section>
    <p v-if="!loading&&!jobs.length&&!privateTargets.length">当前没有可用的已发布岗位，也没有已确认的私人岗位。请先在「我的岗位」里粘贴 JD 并确认，或等待管理员审核资料。</p>
    <router-link to="/memory">查看全部成长记录</router-link>
  </main>
</template>

<style scoped>
.training-page{max-width:1000px;margin:0 auto;padding:24px;color:#20242a}h1{font-size:28px;margin-bottom:8px}h2{font-size:20px}p{max-width:76ch;color:#667085;line-height:1.7}section{margin:24px 0;padding:22px;background:white;border:1px solid #e5e7eb;border-radius:8px}.setup{display:grid;grid-template-columns:1fr 1fr;gap:16px}label{display:block;margin-bottom:12px}select,input{display:block;box-sizing:border-box;width:100%;padding:10px;margin-top:6px;border:1px solid #d0d5dd;border-radius:6px;background:white}.targets ul{padding:0;list-style:none}.targets li{padding:16px 0;border-bottom:1px solid #eef2f6}.targets li div{display:flex;justify-content:space-between;gap:12px}.targets span{color:#667085}summary{cursor:pointer;color:#245f55}button{padding:10px 18px;border:1px solid #d0d5dd;border-radius:6px;cursor:pointer}.primary{background:#245f55;color:white}button:disabled{opacity:.55;cursor:default}[role=alert]{color:#b42318}.launch{border-left:4px solid #245f55}a{color:#245f55}:focus-visible{outline:2px solid #245f55;outline-offset:3px}@media(max-width:640px){.setup{grid-template-columns:1fr}.training-page{padding:16px}}
.candidate-panel{border-left:4px solid #6b7f9e}.candidate-panel h2 small{font-size:12px;font-weight:400;color:#667085}.candidate-panel ul{padding:0;list-style:none}.candidate-panel li{padding:14px 0;border-bottom:1px solid #eef2f6}.candidate-panel li:last-child{border-bottom:0}.candidate-panel li div{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.candidate-notice{background:#f0f7f4;padding:10px 14px;border-radius:6px;color:#245f55}
</style>
