export function dashScopeKey() {
  return process.env.DASHSCOPE_API_KEY?.trim() || '';
}

export function dashScopeVisionModel() {
  return process.env.DASHSCOPE_VISION_MODEL?.trim() || 'qwen3-vl-plus';
}

export function dashScopeOcrKey() {
  return process.env.DASHSCOPE_OCR_API_KEY?.trim() || dashScopeKey();
}

export function dashScopeOcrModel() {
  return process.env.DASHSCOPE_OCR_MODEL?.trim() || '';
}

export function dashScopeOcrBaseUrl() {
  return (process.env.DASHSCOPE_OCR_BASE_URL?.trim() || 'https://dashscope.aliyuncs.com/compatible-mode/v1').replace(/\/+$/, '');
}

/**
 * Qwen OCR's high-precision localization is a DashScope-native task rather than
 * an OpenAI-compatible structured-output request.  Workspace URLs use the same
 * host, but the native API lives below /api/v1.
 */
export function dashScopeOcrNativeBaseUrl() {
  const configured = process.env.DASHSCOPE_OCR_NATIVE_BASE_URL?.trim();
  if (configured) return configured.replace(/\/+$/, '');
  return dashScopeOcrBaseUrl()
    .replace(/\/compatible-mode\/v1$/i, '/api/v1')
    .replace(/\/+$/, '');
}

export function guardApiSecret(request: Request) {
  const configured = process.env.PPT_API_SECRET?.trim();
  if (!configured) return null;
  return request.headers.get('x-ppt-api-secret') === configured
    ? null
    : Response.json({ error: '接口访问未授权。' }, { status: 401 });
}
