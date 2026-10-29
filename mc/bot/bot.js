// 大肥鱼 MC 机器人
// 本地 MC 服务端 + Mineflayer 无头机器人（已弃用 prismarine 网页画面，改真客户端当摄像机）
const mineflayer = require('mineflayer')

const VERSION = process.env.MC_VERSION || '1.20.1'
const OPTS = {
  host: '127.0.0.1',
  port: 25565,
  // ⚠️ MC 用户名必须 ASCII（3-16 位字母数字下划线），中文会被服务端踢出
  // 大肥鱼直译 → BigFatFish
  username: 'BigFatFish',
  version: VERSION,
  auth: 'offline',
}

let bot = null
let idleTimer = null

function fmt(p) {
  return p ? `x=${p.x.toFixed(1)} y=${p.y.toFixed(1)} z=${p.z.toFixed(1)}` : '?'
}

function start() {
  bot = mineflayer.createBot(OPTS)
  // 大脑要在 spawn 之前挂（它自己等 spawn 初始化）
  if (process.env.BOT_BRAIN !== '0') attachBrain(bot)

  bot.once('spawn', () => {
    console.log(`[bot] 进入游戏 (MC ${VERSION}) @ ${fmt(bot.entity.position)}`)
    // 有大脑时由大脑控制移动，别再随机转头
    if (!brain) {
      clearInterval(idleTimer)
      idleTimer = setInterval(() => {
        if (bot.entity) {
          bot.look(Math.random() * Math.PI * 2, (Math.random() - 0.5) * 0.5).catch(() => {})
        }
      }, 4000)
    }
  })

  bot.on('chat', (u, m) => {
    if (u !== OPTS.username) console.log(`[聊天] <${u}> ${m}`)
  })
  bot.on('kicked', (r) => console.log('[bot] 被踢出:', JSON.stringify(r).slice(0, 200)))
  bot.on('error', (e) => console.log('[bot] 错误:', e.message))
  bot.on('end', () => {
    clearInterval(idleTimer)
    console.log('[bot] 连接断开，5 秒后重连…')
    setTimeout(start, 5000)
  })
}

// 大脑：挂载后自己会接管移动/采集（BOT_BRAIN=0 可关掉）
let brain = null
function attachBrain(b) {
  try {
    brain = require('./brain.js')
    brain.attach(b)
    console.log('[bot] 大脑已挂载')
  } catch (e) {
    console.log('[bot] 大脑挂载失败:', e.message)
  }
}

// —— HTTP 控制桥：给主服务 main.py 调用（阶段3 接线），也方便手动测试 ——
// GET /pos             → 位置/血量/饥饿
// GET /go?dir=forward&sec=3 → 走路（dir: forward/back/left/right）
// GET /stop            → 立刻停
// GET /say?text=你好    → 游戏内发言
const http = require('http')
const fs = require('fs')
const hud = require('./hud.js')
http
  .createServer((req, res) => {
    const u = new URL(req.url, 'http://x')
    const q = u.searchParams
    const send = (o) => {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify(o))
    }
    // —— 物品栏叠加层（旁观附身看不到 HUD，用网页补）——
    if (u.pathname === '/hud') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      return res.end(hud.HTML)
    }
    if (u.pathname.startsWith('/icon/')) {
      const id = decodeURIComponent(u.pathname.slice('/icon/'.length)).replace(/\.png$/, '')
      const buf = hud.iconBuffer(id)
      if (!buf) {
        res.statusCode = 404
        return res.end()
      }
      res.setHeader('Content-Type', 'image/png')
      res.setHeader('Cache-Control', 'public, max-age=86400')
      return res.end(buf)
    }
    if (u.pathname === '/debug/prompt') {
      res.setHeader('Content-Type', 'text/plain; charset=utf-8')
      try {
        return res.end(brain ? brain.debugPrompt() : 'brain off')
      } catch (e) {
        return res.end('ERR ' + e.message)
      }
    }
    if (u.pathname === '/hud.json') {
      if (!bot || !bot.entity) return send({ ok: false, err: 'not spawned' })
      try {
        return send(hud.data(bot))
      } catch (e) {
        return send({ ok: false, err: e.message })
      }
    }

    if (!bot || !bot.entity) return send({ ok: false, err: 'not spawned' })
    if (u.pathname === '/pos') {
      const p = bot.entity.position
      const below = bot.blockAt(p.offset(0, -1, 0))
      const body = bot.blockAt(p)
      send({
        ok: true,
        pos: { x: +p.x.toFixed(1), y: +p.y.toFixed(1), z: +p.z.toFixed(1) },
        health: bot.health, food: bot.food, mode: bot.game.gameMode,
        onGround: bot.entity.onGround,
        physics: bot.physicsEnabled,
        vel: { x: +bot.entity.velocity.x.toFixed(2), y: +bot.entity.velocity.y.toFixed(2), z: +bot.entity.velocity.z.toFixed(2) },
        below: below ? below.name : null,
        body: body ? body.name : null,
        controls: Object.entries(bot.controlState || {}).filter(([k, v]) => v).map(([k]) => k),
      })
    } else if (u.pathname === '/go') {
      const dir = q.get('dir') || 'forward'
      const sec = Math.min(parseFloat(q.get('sec') || '3'), 10)
      bot.setControlState(dir, true)
      setTimeout(() => bot.setControlState(dir, false), sec * 1000)
      send({ ok: true, dir, sec })
    } else if (u.pathname === '/stop') {
      ;['forward', 'back', 'left', 'right', 'jump', 'sprint'].forEach((k) => bot.setControlState(k, false))
      send({ ok: true })
    } else if (u.pathname === '/say') {
      bot.chat((q.get('text') || '你好').slice(0, 100))
      send({ ok: true })
    } else if (u.pathname === '/directive') {
      // 弹幕指令入口（阶段3 主服务会调它）
      const t = (q.get('text') || '').slice(0, 200)
      if (!brain) return send({ ok: false, err: 'brain off' })
      brain.setDirective(t)
      send({ ok: true, text: t })
    } else if (u.pathname === '/brain') {
      if (!brain) return send({ ok: false, err: 'brain off' })
      const s = brain.state()
      send({ ok: true, current: s.current, directive: s.directive, events: s.events.slice(-6) })
    } else {
      send({ ok: false, err: 'unknown endpoint' })
    }
  })
  .listen(3008, () => console.log('[http] 控制桥 → http://127.0.0.1:3008/  (/pos /go /stop /say)'))

// —— 控制台直接敲命令控制（演示用；后续这些会由 LLM/弹幕驱动）——
console.log('[控制] go=往前走3秒  stop=停  jump=跳  pos=报状态  quit=退出')
require('readline')
  .createInterface({ input: process.stdin })
  .on('line', (l) => {
    const cmd = l.trim()
    if (!bot || !bot.entity) return console.log('[控制] 还没进游戏，等等')
    if (cmd === 'go') {
      bot.setControlState('forward', true)
      setTimeout(() => bot.setControlState('forward', false), 3000)
    } else if (cmd === 'stop') {
      ;['forward', 'back', 'left', 'right', 'jump', 'sprint'].forEach((k) =>
        bot.setControlState(k, false),
      )
    } else if (cmd === 'jump') {
      bot.setControlState('jump', true)
      setTimeout(() => bot.setControlState('jump', false), 400)
    } else if (cmd === 'pos') {
      console.log('[bot] 位置', fmt(bot.entity.position), '血量', bot.health, '饥饿', bot.food)
    } else if (cmd === 'quit') {
      process.exit(0)
    } else if (cmd) {
      console.log('[控制] 未知命令:', cmd)
    }
  })

start()
