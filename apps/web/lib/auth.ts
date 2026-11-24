import { betterAuth } from "better-auth";
import { nextCookies } from "better-auth/next-js";
import { jwt, organization } from "better-auth/plugins";
import { Pool } from "pg";

// Better Auth connects with the `app_auth` role, which can only touch the
// identity tables. Application tables are reachable only through the API.
const pool = new Pool({ connectionString: process.env.AUTH_DATABASE_URL });

const baseURL = process.env.BETTER_AUTH_URL ?? "http://localhost:3000";

export const JWT_AUDIENCE = "ape-api";

export const auth = betterAuth({
  baseURL,
  database: pool,
  emailAndPassword: {
    enabled: true,
    minPasswordLength: 10,
  },
  plugins: [
    organization({
      // No SMTP in the MVP yet: the invitation link is shown to the inviter in
      // the panel and written to the server log. Only the invitation id is
      // logged, never the invitee's address.
      sendInvitationEmail: async ({ id }) => {
        console.info(
          JSON.stringify({
            msg: "invitation created (email delivery not configured)",
            link: `${baseURL}/accept-invitation/${id}`,
          }),
        );
      },
    }),
    jwt({
      jwt: {
        issuer: baseURL,
        audience: JWT_AUDIENCE,
        expirationTime: "15m",
        // The API trusts only these claims: `sub` (user) and `org_id`
        // (active organization). No profile data is put into the token.
        definePayload: ({ session }) => ({
          org_id: session.activeOrganizationId ?? null,
        }),
      },
    }),
    nextCookies(), // must stay last
  ],
});
