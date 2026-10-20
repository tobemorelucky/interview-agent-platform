<script setup lang="ts">
import type { ReferenceAnswer } from '../types/interview'
defineProps<{ reference?: ReferenceAnswer | null; fallback?: string }>()
</script>

<template>
  <section class="reference" aria-label="参考回答">
    <p class="note">示范回答用于练习，不代表你的真实经历或唯一正确答案。</p>
    <template v-if="reference">
      <h4>回答框架</h4><ul><li v-for="point in reference.answer_framework" :key="point">{{ point }}</li></ul>
      <h4>面试版参考回答</h4><p class="spoken">{{ reference.interview_answer }}</p>
      <h4>加分点</h4><ul><li v-for="point in reference.bonus_points" :key="point">{{ point }}</li></ul>
      <h4>可能追问</h4><ul><li v-for="point in reference.possible_follow_ups" :key="point">{{ point }}</li></ul>
    </template>
    <template v-else><h4>回答要点</h4><p class="spoken">{{ fallback || '该历史场次未生成完整参考回答。' }}</p><p class="note">历史记录保留原要点，不补造完整参考回答。</p></template>
  </section>
</template>

<style scoped>
.reference { padding: 14px; background: #f5f8fc; border-radius: 8px; line-height: 1.7; }
h4 { margin: 14px 0 6px; } .spoken { white-space: pre-wrap; } .note { color: #667085; font-size: 13px; }
</style>
