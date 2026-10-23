export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "";

export class ApiError extends Error {
  constructor(message, { status = 0, detail = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { signal, ...options } = {}) {
  let response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      signal,
      headers: {
        Accept: "application/json",
        ...options.headers,
      },
      ...options,
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw error;
    }

    throw new ApiError("Cannot reach the Kinetic backend service.", {
      detail: error,
    });
  }

  let body = null;

  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const message =
      typeof body?.detail === "string"
        ? body.detail
        : body?.detail?.message || `Request failed with status ${response.status}`;

    throw new ApiError(message, {
      status: response.status,
      detail: body,
    });
  }

  return body;
}

export function getTier1Health({ signal } = {}) {
  return request("/api/v1/kpi/tier1-health", { signal });
}

export function getRootCauseAnalysis({ signal } = {}) {
  return request("/api/v1/analysis/root-cause", { signal });
}

export function getFixWithAI(rootCauseId, { signal } = {}) {
  return request(`/api/v1/analysis/root-causes/${encodeURIComponent(rootCauseId)}/fix-with-ai`, { signal });
}

export function getToBeDesign(rootCauseId, { signal } = {}) {
  return request(`/api/v1/analysis/root-causes/${encodeURIComponent(rootCauseId)}/to-be-design`, { signal });
}

export function getToBeTransformation(rootCauseId, { signal } = {}) {
  return request(`/api/v1/analysis/root-causes/${encodeURIComponent(rootCauseId)}/to-be-transformation`, { signal });
}

export function health({ signal } = {}) {
  return request("/health", { signal });
}

export function createDeploymentPackage(payload, { signal } = {}) {
  return request("/api/v1/agents/deployment-packages", {
    signal,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getDeploymentPackage(packageId, { signal } = {}) {
  return request(`/api/v1/agents/deployment-packages/${encodeURIComponent(packageId)}`, { signal });
}

export function requestDeploymentApproval(packageId, payload = {}, { signal } = {}) {
  return request(`/api/v1/agents/deployment-packages/${encodeURIComponent(packageId)}/request-approval`, {
    signal,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function resetDeploymentApproval(packageId, { signal } = {}) {
  return request(`/api/v1/agents/deployment-packages/${encodeURIComponent(packageId)}/reset-approval`, {
    signal,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export function lookupApprovalToken(token, { signal } = {}) {
  return request(`/api/v1/agents/approval-tokens/${encodeURIComponent(token)}`, { signal });
}

export function decideApproval(token, payload, { signal } = {}) {
  return request(`/api/v1/agents/approval-tokens/${encodeURIComponent(token)}/decision`, {
    signal, method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

export function requestDeploymentChange(packageId, payload = {}, { signal } = {}) {
  return request(`/api/v1/agents/deployment-packages/${encodeURIComponent(packageId)}/change-request`, {
    signal, method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}