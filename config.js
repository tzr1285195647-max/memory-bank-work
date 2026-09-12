/**
 * 前端运行配置。
 *
 * baseUrl 指向本机后端（开发用）。
 * 注意：微信真机不允许请求 http://127.0.0.1，也不允许 IP 地址；
 * 真机调试或上线需要 HTTPS 域名并加入小程序后台白名单（见 docs/TECH_PLAN.md 第 9 节）。
 * 开发者工具演示时请在「详情 -> 本地设置」勾选「不校验合法域名」。
 */

module.exports = {
  baseUrl: 'http://127.0.0.1:8787',
  /** 后端不可用时的表现：true=自动降级到 mock 数据，保证演示不中断 */
  fallbackToMock: true,
};
