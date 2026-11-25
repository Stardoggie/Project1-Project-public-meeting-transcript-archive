<script setup>
import { ref, onMounted } from 'vue'
import { getJson } from '@/api'

const bodies = ref([])
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    bodies.value = await getJson('bodies')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="page">
    <section class="intro">
      <h1>Find out what your local government said.</h1>
      <p class="quiet">
        The governing bodies in the archive. To find out what they said,
        <!-- Django's page: if you're not signed in, Django sends you to sign in first -->
        <a href="/chat/">ask the archive's assistant</a>.
      </p>
    </section>

    <section>
      <h2 class="section-title">Governing bodies</h2>
      <ul v-if="loading" class="card-grid" aria-busy="true">
        <li v-for="n in 3" :key="n" class="card skeleton"></li>
      </ul>
      <p v-else-if="error" class="error">{{ error }}</p>
      <p v-else-if="!bodies.length" class="quiet">No governing bodies yet.</p>
      <ul v-else class="card-grid">
        <li v-for="body in bodies" :key="body.id" class="card body-card">
          <h3>{{ body.name }}</h3>
          <span class="description">{{ body.description }}</span>
          <span class="tags">
            <span class="tag">
              {{ body.meeting_count ?? 0 }} {{ body.meeting_count === 1 ? 'meeting' : 'meetings' }}
            </span>
          </span>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.body-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-left: 4px solid var(--gold);
}

.body-card h3 {
  font: 600 17px/1.25 var(--serif);
  margin: 0;
  overflow-wrap: anywhere;
}

.description {
  font-size: 14px;
  color: var(--muted);
  white-space: pre-line;
}
</style>
