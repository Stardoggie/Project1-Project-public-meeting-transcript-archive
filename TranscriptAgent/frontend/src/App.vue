<script setup>
import { onMounted } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { session, loadSession, signOut } from '@/session'

const route = useRoute()

// Ask Django once, when the app starts, who's signed in
onMounted(loadSession)
</script>

<template>
  <header class="masthead">
    <div class="inner">
      <RouterLink to="/" class="brand">
        <!-- The same columned-building seal as the chatbot page -->
        <svg class="seal" viewBox="0 0 40 40" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M5 15 L20 6 L35 15 Z" stroke-linejoin="round" />
          <path d="M9 18v12M16 18v12M24 18v12M31 18v12" stroke-linecap="round" />
          <path d="M5 33h30" stroke-linecap="round" />
        </svg>
        <span class="name">Meeting Transcript Archive</span>
      </RouterLink>
      <nav>
        <!-- Plain links, not RouterLinks: these are Django's pages, so the browser loads them in full.
             The chatbot needs a sign-in, so Django sends anyone not signed in to the sign-in page first. -->
        <a href="/chat/">Ask the archive</a>
        <a v-if="session.isStaff" href="/admin/">Admin</a>
        <!-- Nothing until Django has answered, so the button doesn't flicker from "Sign in" to "Sign out" -->
        <template v-if="session.loaded">
          <button v-if="session.authenticated" type="button" class="sign-in" @click="signOut">
            Sign out <span class="who">{{ session.username }}</span>
          </button>
          <RouterLink v-else-if="route.name !== 'signin'" :to="{ path: '/signin', query: { next: route.fullPath } }"
            class="sign-in">
            Sign in
          </RouterLink>
        </template>
      </nav>
    </div>
  </header>

  <!-- Each page (src/views) shows here -->
  <RouterView />
</template>

<style scoped>
.masthead {
  background: var(--mast);
  color: var(--mast-text);
  border-bottom: 3px solid var(--gold);
}

.inner {
  max-width: 1040px;
  margin: 0 auto;
  padding: 16px 16px 14px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px 24px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
  color: inherit;
  text-decoration: none;
}

.seal {
  width: 40px;
  height: 40px;
  flex: none;
  color: var(--gold);
}

.name {
  font: 600 22px/1.2 var(--serif);
  letter-spacing: 0.2px;
}

nav {
  display: flex;
  align-items: center;
  gap: 18px;
}

nav a {
  color: var(--mast-text);
  text-decoration: none;
  font-size: 14px;
}

nav a:hover {
  text-decoration: underline;
}

/* The chatbot page's masthead buttons: see-through with a faint white border */
.sign-in {
  font: inherit;
  font-size: 14px;
  color: var(--mast-text);
  text-decoration: none;
  background: transparent;
  cursor: pointer;
  padding: 6px 14px;
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 8px;
}

/* border-color undoes main.css's blue border for buttons on hover */
.sign-in:hover {
  text-decoration: none;
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.28);
}

.who {
  color: var(--mast-muted);
}

@media (max-width: 560px) {
  .who {
    display: none;
  }

  .name {
    font-size: 18px;
  }
}
</style>
