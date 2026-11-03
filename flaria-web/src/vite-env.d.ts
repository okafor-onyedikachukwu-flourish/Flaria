/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_WS_URL?: string;
  readonly VITE_MEDIAPIPE_WASM_BASE?: string;
  readonly VITE_FACE_MODEL_URL?: string;
  readonly VITE_DEMO_MODE?: string;
  readonly VITE_APP_URL?: string;
  readonly VITE_SITE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
