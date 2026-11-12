<script setup lang="ts">
import { ref, watch } from 'vue'
import { trainingFeedback, type TrainingFeedback } from '../api/training'
import { capabilityLabels } from '../types/capability-memory'
const props=defineProps<{sessionId:number}>()
const data=ref<TrainingFeedback|null>(null),error=ref(''),loading=ref(false)
let sequence=0
async function load(){const current=++sequence;loading.value=true;error.value='';try{const value=await trainingFeedback(props.sessionId);if(current===sequence)data.value=value}catch(e){if(current===sequence)error.value=e instanceof Error?e.message:'训练反馈读取失败'}finally{if(current===sequence)loading.value=false}}
watch(()=>props.sessionId,load,{immediate:true})
const conflict:Record<string,string>={OPEN:'冲突仍未解决',RESOLVED:'已有独立复核支持冲突解决',UNKNOWN:'冲突结果尚未确认'}
</script>
<template>
  <article class="training-feedback" v-if="loading||error||data?.available">
    <h2>本次训练带来了什么变化</h2><p v-if="loading" role="status">正在核对有效评价和能力版本…</p>
    <p v-if="error" role="alert">{{error}} <button @click="load">重试</button></p>
    <template v-if="data"><p>完成时的状态与来源记录，不是掌握百分比；后续更正不会改写本场冻结结果。独立作答计数不等于验证成功，提示后答对仍不算独立掌握。</p><p v-if="data.message">{{data.message}}</p>
      <section v-for="t in data.targets" :key="t.knowledge_id"><h3>{{t.label}}</h3><p>训练前：{{capabilityLabels[t.before_state]}}　训练后：{{capabilityLabels[t.after_state]}}</p>
        <p>{{t.result==='NO_NEW_OBSERVATIONS'?'尚无足够新观察，不据此判定进步。':`有效评价 ${t.effective_evaluations} 条，其中独立作答 ${t.independent_evaluations} 条。`}} {{conflict[t.conflict_status]}}</p><p>下一步：{{t.next_action}}</p>
      </section>
      <details v-if="data.questions?.length"><summary>查看实际考察过程</summary><p v-for="q in data.questions" :key="q.question_id">{{q.strategy==='NEXT'?'切换 / 开场':q.strategy==='DEEP_DIVE'?'技术深挖':q.strategy==='REQUEST_EXAMPLE'?'要求具体例子':'针对性追问'}}：{{q.question}}</p></details>
      <details v-if="data.memory_usage?.length"><summary>记忆计划与实际考察说明</summary><p>准备输入不等于模型一定采纳；选择相关目标也不证明因果收益。无法核对的阶段保留未知。</p><p v-for="(u,i) in data.memory_usage" :key="u.run_id">第 {{i+1}} 次出题：{{u.model_input==='PREPARED'?'已准备包含冻结计划的模型输入':'输入记录未知'}}；{{u.selection==='RELATED_TARGET_SELECTED'?'选择了计划相关目标':u.selection==='OTHER_TARGET_SELECTED'?'选择了其他合法目标':'目标选择未知'}}；{{u.controller_verdict==='ACCEPTED'?'已通过会话校验并提交':u.controller_verdict==='REJECTED'?'被会话校验拒绝':u.controller_verdict==='NOT_REACHED'?'未进入会话提交':'提交裁决未知'}}。</p></details>
      <router-link v-if="data.job_target_id" :to="{path:'/training',query:{job_target_id:data.job_target_id,recommended_skill_id:data.skill_id}}">根据当前状态继续本私人岗位训练</router-link>
      <router-link v-else-if="data.job_id" :to="{path:'/training',query:{company_id:data.company_id,job_id:data.job_id,recommended_skill_id:data.skill_id}}">根据当前状态选择下一步训练</router-link>
    </template>
  </article>
</template>
<style scoped>.training-feedback{padding:22px;background:white;border:1px solid #e5e7eb;border-left:4px solid #245f55;border-radius:8px;margin-bottom:20px}p{max-width:76ch;color:#667085;line-height:1.7}section{padding:12px 0;border-bottom:1px solid #eef2f6}a,summary{color:#245f55}summary{cursor:pointer;margin:12px 0}</style>
