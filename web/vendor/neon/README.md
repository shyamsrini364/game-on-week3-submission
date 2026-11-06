# Locally bundled Neon Auth client

Source: @neon/auth 0.8.2 (Apache-2.0), https://github.com/neondatabase/neon-pkgs.
Build from repository root with npm ci --ignore-scripts followed by npm run build:auth.
package-lock.json pins transitive dependencies. esbuild retains dependency legal comments in auth.js; the Neon package license is included here.
No credentials are embedded. The public branch URL comes from /api/auth/config.
