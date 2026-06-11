import axios from "axios";

const api = axios.create({
  baseURL: `${process.env.REACT_APP_BACKEND_URL}/api`,
  withCredentials: true,
});

export function errText(e) {
  const detail = e?.response?.data?.detail;
  if (detail == null) return e?.message || "请求失败，请重试";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((x) => (x && typeof x.msg === "string" ? x.msg : JSON.stringify(x)))
      .filter(Boolean)
      .join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

export default api;
