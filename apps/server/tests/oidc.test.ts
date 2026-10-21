import { createServer } from 'node:http';
import { exportJWK, generateKeyPair, SignJWT } from 'jose';
import { describe, expect, it } from 'vitest';
import { startTestServer, setupAdmin, type TestServer } from './helpers.js';

interface OidcHarness {
  server: TestServer;
  baseUrl: string;
  setUserinfoSub: (sub: string) => void;
  signToken: (input: {
    nonce: string;
    email: string;
    emailVerified: boolean;
    sub?: string;
    issuer?: string;
  }) => Promise<string>;
  close: () => Promise<void>;
}

async function startOidcHarness(options: { wrongDiscoveryIssuer?: boolean } = {}): Promise<OidcHarness> {
  const { publicKey, privateKey } = await generateKeyPair('RS256');
  const jwk = { ...(await exportJWK(publicKey)), kid: 'test-key', use: 'sig', alg: 'RS256' };
  let idToken = '';
  let userinfoSub = 'oidc-sub-1';
  const provider = createServer(async (req, res) => {
    const base = `http://127.0.0.1:${(provider.address() as { port: number }).port}`;
    if (req.url === '/.well-known/openid-configuration') {
      res.writeHead(200, { 'content-type': 'application/json' }).end(
        JSON.stringify({
          issuer: options.wrongDiscoveryIssuer ? `${base}/wrong` : base,
          authorization_endpoint: `${base}/authorize`,
          token_endpoint: `${base}/token`,
          jwks_uri: `${base}/jwks`,
          userinfo_endpoint: `${base}/userinfo`,
        }),
      );
      return;
    }
    if (req.url === '/jwks') {
      res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ keys: [jwk] }));
      return;
    }
    if (req.url === '/token') {
      for await (const _chunk of req) {
        // consume form body
      }
      res.writeHead(200, { 'content-type': 'application/json' }).end(
        JSON.stringify({ id_token: idToken, access_token: 'access-token', token_type: 'Bearer' }),
      );
      return;
    }
    if (req.url === '/userinfo') {
      res.writeHead(200, { 'content-type': 'application/json' }).end(
        JSON.stringify({ sub: userinfoSub, email: 'existing@example.test', name: 'OIDC 用户' }),
      );
      return;
    }
    res.writeHead(404).end();
  });
  await new Promise<void>((resolve) => provider.listen(0, '127.0.0.1', resolve));
  const baseUrl = `http://127.0.0.1:${(provider.address() as { port: number }).port}`;
  const server = await startTestServer({
    autoStartWorker: false,
    oidc: {
      issuer: baseUrl,
      clientId: 'adp-client',
      clientSecret: 'adp-secret',
      redirectUri: 'http://localhost:3001/api/auth/oidc/callback',
      scopes: 'openid profile email',
    },
  });
  return {
    server,
    baseUrl,
    setUserinfoSub: (sub) => {
      userinfoSub = sub;
    },
    signToken: async (input) => {
      idToken = await new SignJWT({
        nonce: input.nonce,
        email: input.email,
        email_verified: input.emailVerified,
        name: 'OIDC 用户',
      })
        .setProtectedHeader({ alg: 'RS256', kid: 'test-key' })
        .setIssuer(input.issuer ?? baseUrl)
        .setAudience('adp-client')
        .setSubject(input.sub ?? 'oidc-sub-1')
        .setIssuedAt()
        .setExpirationTime('5m')
        .sign(privateKey);
      return idToken;
    },
    close: async () => {
      await server.close();
      await new Promise<void>((resolve) => provider.close(() => resolve()));
    },
  };
}

async function beginOidc(server: TestServer) {
  const response = await server.app.inject({ method: 'GET', url: '/api/auth/oidc/start' });
  expect(response.statusCode).toBe(302);
  const location = new URL(String(response.headers.location));
  const setCookie = response.headers['set-cookie'];
  const cookies = Array.isArray(setCookie) ? setCookie : [String(setCookie)];
  const stateCookie = cookies.find((cookie) => cookie.startsWith('adp_oidc_state='))!.split(';')[0];
  return {
    state: location.searchParams.get('state')!,
    nonce: location.searchParams.get('nonce')!,
    stateCookie,
  };
}

async function callback(server: TestServer, state: string, cookie: string) {
  return server.app.inject({
    method: 'GET',
    url: `/api/auth/oidc/callback?code=ok&state=${state}`,
    headers: { cookie },
  });
}

describe('OIDC login hardening', () => {
  it('binds state to the browser, requires verified email and validates userinfo subject', async () => {
    const harness = await startOidcHarness();
    try {
      const setup = await setupAdmin(harness.server.client);
      const local = await harness.server.client.request('POST', '/api/users', {
        name: '已有本地用户',
        email: 'existing@example.test',
        password: 'Password123!',
        orgId: setup.organizations[0].id,
      });
      expect(local.data.id).toBeTruthy();
      const status = await harness.server.client.request('GET', '/api/auth/status');
      expect(status.data.oidcEnabled).toBe(true);

      const start = await beginOidc(harness.server);
      expect(start.stateCookie).toContain('adp_oidc_state=');
      const token = await harness.signToken({
        nonce: start.nonce,
        email: 'existing@example.test',
        emailVerified: true,
        sub: 'oidc-sub-1',
      });
      expect(token).toBeTruthy();
      const success = await callback(harness.server, start.state, start.stateCookie);
      expect(success.statusCode).toBe(302);
      const cookies = success.headers['set-cookie'];
      const cookieHeader = (Array.isArray(cookies) ? cookies : [String(cookies)])
        .map((cookie) => cookie.split(';')[0])
        .join('; ');
      const me = await harness.server.app.inject({
        method: 'GET',
        url: '/api/auth/me',
        headers: { cookie: cookieHeader },
      });
      expect(me.statusCode).toBe(200);
      expect(JSON.parse(me.body).user.id).toBe(local.data.id);

      const mismatch = await beginOidc(harness.server);
      const mismatchCallback = await callback(harness.server, mismatch.state, 'adp_oidc_state=attacker-state');
      expect(mismatchCallback.statusCode).toBe(400);
      expect(JSON.parse(mismatchCallback.body).error).toBe('OIDC_STATE_INVALID');

      const unverified = await beginOidc(harness.server);
      await harness.signToken({
        nonce: unverified.nonce,
        email: 'existing@example.test',
        emailVerified: false,
        sub: 'oidc-sub-2',
      });
      harness.setUserinfoSub('oidc-sub-2');
      const unverifiedCallback = await callback(harness.server, unverified.state, unverified.stateCookie);
      expect(unverifiedCallback.statusCode).toBe(401);
      expect(JSON.parse(unverifiedCallback.body).error).toBe('OIDC_EMAIL_UNVERIFIED');

      const subMismatch = await beginOidc(harness.server);
      await harness.signToken({
        nonce: subMismatch.nonce,
        email: 'existing@example.test',
        emailVerified: true,
        sub: 'oidc-sub-3',
      });
      harness.setUserinfoSub('different-sub');
      const subCallback = await callback(harness.server, subMismatch.state, subMismatch.stateCookie);
      expect(subCallback.statusCode).toBe(401);
      expect(JSON.parse(subCallback.body).error).toBe('OIDC_SUBJECT_MISMATCH');
    } finally {
      await harness.close();
    }
  });

  it('rejects discovery documents whose issuer does not match configuration', async () => {
    const harness = await startOidcHarness({ wrongDiscoveryIssuer: true });
    try {
      const start = await harness.server.app.inject({ method: 'GET', url: '/api/auth/oidc/start' });
      expect(start.statusCode).toBe(502);
      expect(JSON.parse(start.body).error).toBe('OIDC_DISCOVERY_FAILED');
    } finally {
      await harness.close();
    }
  });
});
