/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_REPOSITORY_URL?: string;
  readonly VITE_DOCS_URL?: string;
  readonly VITE_SITE_BASE_PATH?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
