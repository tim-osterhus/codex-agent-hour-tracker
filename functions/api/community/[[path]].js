import { routeCommunity } from "../../../community/api.js";

export async function onRequest(context) {
  return routeCommunity(context.request, context.env, {
    fetchImpl: globalThis.fetch,
  });
}
