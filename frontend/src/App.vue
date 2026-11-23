<template>
  <router-view />
  <SessionExpiredDialog />
</template>

<script setup>
import SessionExpiredDialog from '@/components/SessionExpiredDialog.vue'
</script>

<style>
@import '@/assets/styles/main.css';
</style>
