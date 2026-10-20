<script setup lang="ts">
import type { DesignTrace } from '../types/interview'
defineProps<{ trace?: DesignTrace }>()
const labels: Record<string, string> = { NONE: '无保护', CHECK_THEN_WRITE: '先检查再写入（非原子）', ATOMIC_KEY: '原子业务键去重', ATOMIC_RESERVATION: '原子条件扣减名额' }
</script>
<template>
  <section v-if="trace?.design_revisions?.length" class="ux-card" aria-label="方案修订与真实验证">
    <h3>方案修订与验证结果</h3>
    <p>程序执行有界交错模拟，不运行你的代码，不代表真实生产环境压测。</p>
    <details v-for="revision in trace.design_revisions" :key="revision.version" :open="revision.version === trace.design_revisions.length">
      <summary>第 {{ revision.version }} 版方案</summary>
      <p>去重：{{ labels[revision.plan.deduplication] }}；名额保护：{{ labels[revision.plan.capacity_guard] }}；容量 {{ revision.plan.capacity }}</p>
      <div v-for="tool in trace.design_tools?.filter(t => t.plan_version === revision.version)" :key="tool.id">
        <strong>{{ tool.tool === 'verify_idempotency' ? '重复请求验证' : '并发名额验证' }}</strong>
        <p>{{ tool.status === 'SUCCEEDED' ? '执行完成' : tool.status === 'FAILED' ? '执行失败，可安全重试' : '验证处理中；刷新可恢复状态' }}</p>
        <p v-if="tool.result">{{ tool.result.passed ? '本次模拟通过' : '本次模拟发现问题' }}：提交 {{ tool.result.submitted_count }} 次，实际接受 {{ tool.result.accepted_count }} 次。</p>
        <p v-if="tool.result?.violation">{{ tool.result.violation === 'DUPLICATE_RESERVATION' ? '同一请求产生了重复预约' : '接受的预约超过容量' }}</p>
      </div>
    </details>
  </section>
</template>
