<script setup>
import { ref, computed } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { session, signIn } from '@/session'

const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

// Where to go afterwards. Only a path on this site: "//other.site" or "https://..." would send people elsewhere.
const next = computed(() => {
  const value = route.query.next
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : '/'
})
const forChatbot = computed(() => next.value.startsWith('/chat/'))

// Pages Vue doesn't have (the chatbot, the admin) are Django's, so the browser loads them in full
function goOn() {
  if (router.resolve(next.value).matched.length) {
    router.push(next.value)
  } else {
    window.location.assign(next.value)
  }
}

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await signIn(username.value, password.value)
    goOn()
  } catch (e) {
    error.value = e.message
    password.value = ''
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="page">
    <div class="card panel">
      <h1>Sign in</h1>

      <template v-if="session.authenticated">
        <p>You're signed in as <strong>{{ session.username }}</strong>.</p>
        <p><button type="button" class="primary" @click="goOn">Continue</button></p>
      </template>

      <template v-else>
        <p v-if="forChatbot" class="notice">
          Sign in first to ask the archive. You'll go straight to the assistant afterwards.
        </p>
        <p v-if="error" class="error" role="alert">{{ error }}</p>

        <form @submit.prevent="submit">
          <label for="username">Username</label>
          <input id="username" v-model="username" type="text" autocomplete="username" autofocus required />
          <label for="password">Password</label>
          <input id="password" v-model="password" type="password" autocomplete="current-password" required />
          <button type="submit" class="primary" :disabled="busy">{{ busy ? 'Signing in…' : 'Sign in' }}</button>
        </form>

        <p class="quiet">
          Accounts are made by the archive's clerks. <RouterLink to="/">Back to the home page</RouterLink>
        </p>
      </template>
    </div>
  </main>
</template>

<style scoped>
.panel {
  max-width: 440px;
  margin: 20px auto 0;
  padding: 28px;
}

h1 {
  font: 600 26px/1.2 var(--serif);
  margin: 0 0 8px;
}

.notice {
  background: var(--accent-soft);
  border-left: 4px solid var(--gold);
  border-radius: 8px;
  padding: 10px 14px;
}

label {
  display: block;
  margin: 14px 0 4px;
  font-weight: 600;
  font-size: 14px;
}

input {
  width: 100%;
  min-height: 44px;
  padding: 9px 12px;
  font: inherit;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
}

form button {
  margin-top: 20px;
  min-height: 44px;
  padding: 0 18px;
}

button:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
