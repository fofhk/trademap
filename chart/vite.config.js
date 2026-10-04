import { defineConfig } from 'vite';

// Relative asset paths: chartserve.py serves dist/ at the site root, but nothing
// here should care where it is mounted.
export default defineConfig({ base: './' });
