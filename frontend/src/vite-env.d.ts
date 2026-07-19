// Local fallback types for Vite client to satisfy "types": ["vite/client"]
// Safe to keep; will be ignored if real types are present in node_modules.

declare module 'vite/client' {
  interface ImportMetaEnv {
    readonly VITE_API_URL?: string;
  }

  interface ImportMeta {
    readonly env: ImportMetaEnv;
  }
}
