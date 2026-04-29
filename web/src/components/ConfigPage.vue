<template>
  <div class="mt-6 space-y-6">
    <div class="glass-card overflow-hidden p-6">
      <div class="pointer-events-none absolute"></div>
      <div class="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div class="mb-3 inline-flex items-center gap-2 rounded-full border border-blue-400/20 bg-blue-500/10 px-4 py-2 text-sm text-blue-200">
            <span class="inline-block h-2 w-2 rounded-full bg-cyan-400 shadow-[0_0_14px_rgba(34,211,238,0.9)]"></span>
            AutoTeam Configuration Center
          </div>
          <h2 class="section-heading">配置面板</h2>
          <p class="section-subtitle max-w-2xl">
            按邮箱服务、远端同步、安全、管理员、巡检、源文件编辑和代理拆成独立分区，避免把所有运行配置堆在一个页面里。
          </p>
        </div>

        <div class="status-badge max-w-sm text-xs leading-6 text-slate-400">
          高频配置前置，低频配置后置；代理等高级项默认折叠，源文件编辑仍然保留。
        </div>
      </div>

      <div class="mt-6 grid gap-4 md:grid-cols-3">
        <div class="glass-card-soft p-4">
          <div class="text-2xl">🧩</div>
          <div class="mt-3 text-sm font-medium text-white">独立配置分区</div>
            <div class="mt-1 text-xs leading-5 text-slate-400">邮箱服务、同步、安全等高频项前置，低频代理项后置，不再混在一张表单里。</div>
        </div>
        <div class="glass-card-soft p-4">
          <div class="text-2xl">☁️</div>
          <div class="mt-3 text-sm font-medium text-white">动态同步配置</div>
          <div class="mt-1 text-xs leading-5 text-slate-400">先选择邮箱提供者 / 启用目标，再按状态展示对应配置。</div>
        </div>
        <div class="glass-card-soft p-4">
          <div class="text-2xl">📝</div>
          <div class="mt-3 text-sm font-medium text-white">源文件编辑保留</div>
          <div class="mt-1 text-xs leading-5 text-slate-400">可视化配置之外，仍可直接维护完整 .env 源文件。</div>
        </div>
      </div>
    </div>

    <div class="glass-card p-4">
      <div class="flex flex-wrap gap-2">
        <button
          v-for="item in visualCategories"
          :key="item.key"
          @click="visualCategory = item.key"
          class="pill-tab flex items-center gap-2"
          :class="visualCategory === item.key
            ? 'pill-tab-active'
            : ''"
        >
          <span class="text-base">{{ item.icon }}</span>
          {{ item.label }}
        </button>
      </div>
    </div>

    <div
      v-if="selectedRuntimeCategory"
      class="glass-card p-6"
    >
      <div class="mb-6 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div class="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">
            <span>{{ currentRuntimeCategoryMeta?.icon }}</span>
            {{ currentRuntimeCategoryMeta?.badge }}
          </div>
          <h3 class="section-heading">{{ currentRuntimeCategoryMeta?.title }}</h3>
          <p class="section-subtitle max-w-3xl">
            {{ currentRuntimeCategoryMeta?.description }}
          </p>
          <p
            v-if="currentRuntimeCategoryMeta?.note"
            class="mt-2 text-xs text-slate-500"
          >
            {{ currentRuntimeCategoryMeta.note }}
          </p>
        </div>
        <div class="flex items-center gap-3">
          <span
            v-if="runtimeSaved"
            class="status-badge border-emerald-400/20 bg-emerald-500/10 text-emerald-200"
          >
            已保存
          </span>
          <span
            class="status-badge min-w-[84px] justify-center"
            :class="currentRuntimeStatus.class"
          >
            {{ currentRuntimeStatus.label }}
          </span>
        </div>
      </div>

      <div
        v-if="runtimeMessage"
        class="mb-4 rounded-2xl px-4 py-3 text-sm border"
        :class="runtimeMessageClass"
      >
        {{ runtimeMessage }}
      </div>

      <div v-if="runtimeLoading" class="text-sm text-slate-400">
        正在加载当前配置...
      </div>

      <div v-else-if="selectedRuntimeCategory === 'cloudmail'" class="space-y-5">
        <div class="rounded-2xl border border-white/10 bg-white/5 p-5">
          <div class="mb-4 flex items-center justify-between gap-4">
            <div>
              <div class="text-sm font-medium text-white">邮箱提供者</div>
              <div class="mt-1 text-xs leading-5 text-slate-400">
                先选择当前用于创建临时邮箱、收验证码和自动复用的邮箱后端。
              </div>
            </div>
            <div class="status-badge text-xs text-slate-400">
              {{ selectedMailProvider === 'cloudflare_temp_email' ? 'Cloudflare Temp Email' : 'CloudMail' }}
            </div>
          </div>
          <select v-model="runtimeForm.MAIL_PROVIDER" class="input-dark">
            <option value="cloudmail">CloudMail</option>
            <option value="cloudflare_temp_email">Cloudflare Temp Email</option>
          </select>
        </div>

        <div v-if="selectedMailProvider === 'cloudmail'" class="rounded-2xl border border-white/10 bg-white/5 p-5">
          <div class="mb-4">
            <div class="text-sm font-medium text-white">CloudMail</div>
            <div class="mt-1 text-xs leading-5 text-slate-400">
              填写 CloudMail API 地址、管理员账号和用于创建临时邮箱的域名。
            </div>
          </div>
          <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div v-for="field in cloudmailProviderFields" :key="field.key" class="rounded-2xl border border-white/10 bg-slate-950/25 p-4">
              <label class="mb-2 block text-sm font-medium text-slate-300">
                {{ field.prompt }}
                <span v-if="isRuntimeRequired(field)" class="text-red-400">*</span>
              </label>
              <input
                v-model="runtimeForm[field.key]"
                :type="fieldInputType(field.key)"
                :placeholder="field.default || ''"
                :autocomplete="fieldAutocomplete(field.key)"
                :name="`runtime-${field.key}`"
                class="input-dark"
              />
            </div>
          </div>
        </div>

        <div v-else-if="selectedMailProvider === 'cloudflare_temp_email'" class="rounded-2xl border border-white/10 bg-white/5 p-5">
          <div class="mb-4">
            <div class="text-sm font-medium text-white">Cloudflare Temp Email</div>
            <div class="mt-1 text-xs leading-5 text-slate-400">
              填写 Cloudflare Temp Email 管理端地址、管理员密码和默认邮箱域名。
            </div>
          </div>
          <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div v-for="field in cfTempEmailFields" :key="field.key" class="rounded-2xl border border-white/10 bg-slate-950/25 p-4">
              <label class="mb-2 block text-sm font-medium text-slate-300">
                {{ field.prompt }}
                <span v-if="isRuntimeRequired(field)" class="text-red-400">*</span>
              </label>
              <input
                v-model="runtimeForm[field.key]"
                :type="fieldInputType(field.key)"
                :placeholder="field.default || ''"
                :autocomplete="fieldAutocomplete(field.key)"
                :name="`runtime-${field.key}`"
                class="input-dark"
              />
            </div>
          </div>
        </div>

        <div class="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 lg:flex-row lg:items-center lg:justify-between">
          <p class="text-xs leading-6 text-slate-400">
            保存后会立即热加载；后续创建账号、自动收验证码和自动复用都会改用当前选择的邮箱提供者。
          </p>
          <button
            @click="saveRuntimeConfig"
            :disabled="runtimeSaving || runtimeLoading"
            class="btn-primary"
          >
            {{ runtimeSaving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </div>

      <div v-else-if="selectedRuntimeCategory === 'sync'" class="space-y-5">
        <div class="rounded-2xl border border-white/10 bg-white/5 p-5">
          <div class="mb-4 flex items-center justify-between gap-4">
            <div>
              <div class="text-sm font-medium text-white">同步目标开关</div>
              <div class="mt-1 text-xs leading-5 text-slate-400">
                可同时启用多个远端。界面只展示当前已启用目标的详细配置。
              </div>
            </div>
            <div class="status-badge text-xs text-slate-400">
              {{ enabledSyncTargetsText }}
            </div>
          </div>
          <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div v-for="field in syncToggleFields" :key="field.key" class="rounded-2xl border border-white/10 bg-slate-950/25 p-4">
              <label class="mb-2 block text-sm font-medium text-slate-300">
                {{ field.prompt }}
              </label>
              <select
                v-model="runtimeForm[field.key]"
                class="input-dark"
              >
                <option value="true">启用</option>
                <option value="false">关闭</option>
              </select>
            </div>
          </div>
        </div>

        <div v-if="syncSub2apiEnabled" class="rounded-2xl border border-white/10 bg-white/5 p-5">
          <div class="mb-4">
            <div class="text-sm font-medium text-white">Sub2API</div>
            <div class="mt-1 text-xs leading-5 text-slate-400">
              为已启用的 Sub2API 远端填写地址、管理员邮箱、密码和可选分组。
            </div>
          </div>
          <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div v-for="field in syncSub2apiConnectionFields" :key="field.key" class="rounded-2xl border border-white/10 bg-slate-950/25 p-4">
              <label class="mb-2 block text-sm font-medium text-slate-300">
                {{ field.prompt }}
                <span v-if="isRuntimeRequired(field)" class="text-red-400">*</span>
                <div v-if="sub2apiFieldHint(field.key)" class="mt-1 font-mono text-[11px] font-normal text-slate-500 break-all">
                  {{ sub2apiFieldHint(field.key) }}
                </div>
              </label>
              <input
                v-model="runtimeForm[field.key]"
                :type="fieldInputType(field.key)"
                :placeholder="field.default || ''"
                :autocomplete="fieldAutocomplete(field.key)"
                :name="`runtime-${field.key}`"
                class="input-dark"
              />
            </div>
          </div>
        </div>

        <div v-if="syncSub2apiEnabled" class="rounded-2xl border border-white/10 bg-white/5 p-5">
          <div class="mb-4">
            <div class="text-sm font-medium text-white">Sub2API 默认账号设置</div>
            <div class="mt-1 text-xs leading-5 text-slate-400">
              新创建的 Sub2API 账号会自动带上这些默认参数和可选代理绑定；已存在账号默认不覆盖，只有开启“覆盖账号设置”后才会在每次同步时强制统一（代理绑定仍只在新建账号时写入）。
            </div>
          </div>
          <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div v-for="field in syncSub2apiDefaultFields" :key="field.key" class="rounded-2xl border border-white/10 bg-slate-950/25 p-4">
              <label class="mb-2 block text-sm font-medium text-slate-300">
                {{ field.prompt }}
                <span v-if="isRuntimeRequired(field)" class="text-red-400">*</span>
                <div v-if="sub2apiFieldHint(field.key)" class="mt-1 font-mono text-[11px] font-normal text-slate-500 break-all">
                  {{ sub2apiFieldHint(field.key) }}
                </div>
              </label>
              <select
                v-if="isBooleanStringField(field.key)"
                v-model="runtimeForm[field.key]"
                class="input-dark"
              >
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
              <select
                v-else-if="isWsModeField(field.key)"
                v-model="runtimeForm[field.key]"
                class="input-dark"
              >
                <option value="off">off</option>
                <option value="ctx_pool">ctx_pool</option>
                <option value="passthrough">passthrough</option>
              </select>
              <input
                v-else
                v-model="runtimeForm[field.key]"
                :type="fieldInputType(field.key)"
                :step="fieldInputStep(field.key)"
                :placeholder="field.default || ''"
                :autocomplete="fieldAutocomplete(field.key)"
                :name="`runtime-${field.key}`"
                class="input-dark"
              />
            </div>
          </div>
        </div>

        <div v-if="!syncSub2apiEnabled" class="rounded-2xl border border-white/10 bg-white/5 px-4 py-4 text-sm text-slate-400">
          当前还没有启用 Sub2API 同步目标。先打开上面的开关，再填写对应远端配置。
        </div>

        <div class="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 lg:flex-row lg:items-center lg:justify-between">
          <p class="text-xs leading-6 text-slate-400">
            保存后会立即热加载；账号池操作会根据当前已启用远端决定后续同步行为。
          </p>
          <button
            @click="saveRuntimeConfig"
            :disabled="runtimeSaving || runtimeLoading"
            class="btn-primary"
          >
            {{ runtimeSaving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </div>

      <div v-else-if="selectedRuntimeCategory === 'proxy'" class="space-y-4">
        <div class="rounded-2xl border border-white/10 bg-white/5 p-4">
          <button
            @click="proxyExpanded = !proxyExpanded"
            class="flex w-full items-center justify-between gap-4 text-left"
          >
            <div>
              <div class="text-sm font-medium text-white">高级代理设置</div>
              <div class="mt-1 text-xs leading-5 text-slate-400">
                低频配置，默认折叠。只有浏览器流量需要单独代理时才建议填写。
              </div>
            </div>
            <span class="text-xs text-slate-400">{{ proxyExpanded ? '收起' : '展开' }}</span>
          </button>

          <div v-if="proxyExpanded" class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div v-for="field in proxyFields" :key="field.key" class="rounded-2xl border border-white/10 bg-slate-950/25 p-4">
              <label class="mb-2 block text-sm font-medium text-slate-300">
                {{ field.prompt }}
                <span v-if="isRuntimeRequired(field)" class="text-red-400">*</span>
              </label>
              <input
                v-model="runtimeForm[field.key]"
                :type="fieldInputType(field.key)"
                :placeholder="field.default || ''"
                :autocomplete="fieldAutocomplete(field.key)"
                :name="`runtime-${field.key}`"
                class="input-dark"
              />
            </div>
          </div>
        </div>

        <div class="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 lg:flex-row lg:items-center lg:justify-between">
          <p class="text-xs leading-6 text-slate-400">
            推荐只在确实需要代理 Playwright 浏览器流量时启用，并配合绕过列表避免本地回调误走代理。
          </p>
          <button
            @click="saveRuntimeConfig"
            :disabled="runtimeSaving || runtimeLoading"
            class="btn-primary"
          >
            {{ runtimeSaving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </div>

      <div v-else-if="selectedRuntimeCategory === 'hero_sms'" class="space-y-5">
        <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
          <!-- API Key / Base URL / Operator / 数值字段:走普通输入 -->
          <div
            v-for="field in heroSmsFields.filter(f => !['HERO_SMS_COUNTRY', 'HERO_SMS_SERVICE'].includes(f.key))"
            :key="field.key"
            class="rounded-2xl border border-white/10 bg-white/5 p-4"
          >
            <label class="mb-2 block text-sm font-medium text-slate-300">
              {{ field.prompt }}
            </label>
            <input
              v-model="runtimeForm[field.key]"
              :type="fieldInputType(field.key)"
              :placeholder="field.default || ''"
              :autocomplete="fieldAutocomplete(field.key)"
              :name="`runtime-${field.key}`"
              class="input-dark"
            />
          </div>

          <!-- 国家:可搜索下拉 + 文本兜底 -->
          <div class="rounded-2xl border border-white/10 bg-white/5 p-4 md:col-span-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <label class="block text-sm font-medium text-slate-300">
                {{ fieldByKey('HERO_SMS_COUNTRY')?.prompt || '国家' }}
              </label>
              <div class="flex flex-wrap gap-2">
                <button
                  type="button"
                  class="btn-secondary text-xs"
                  :disabled="heroSmsCountriesLoading"
                  @click="loadHeroSmsCountries"
                >
                  {{ heroSmsCountriesLoading
                      ? '加载中...'
                      : (heroSmsCountries.length ? `刷新国家(已加载 ${heroSmsCountries.length})` : '加载国家列表') }}
                </button>
                <button
                  type="button"
                  class="btn-secondary text-xs"
                  :disabled="heroSmsAvailabilityLoading"
                  @click="loadHeroSmsAvailability"
                >
                  {{ heroSmsAvailabilityLoading
                      ? '查询中...'
                      : `查 ${runtimeForm.HERO_SMS_SERVICE || 'dr'} 实时号库` }}
                </button>
              </div>
            </div>
            <div class="mt-3 flex flex-wrap items-center gap-3">
              <input
                v-model="runtimeForm.HERO_SMS_COUNTRY"
                type="text"
                placeholder="hero-sms 自家国家 ID,187=USA、0=Russia、3=China"
                autocomplete="off"
                name="runtime-HERO_SMS_COUNTRY"
                class="input-dark flex-1 min-w-[180px]"
              />
              <button
                type="button"
                class="btn-secondary text-xs"
                :disabled="!heroSmsCountries.length"
                @click="heroSmsCountryDropdownOpen = !heroSmsCountryDropdownOpen"
              >
                {{ heroSmsCountryDropdownOpen ? '收起选择器' : '展开搜索选择器' }}
              </button>
            </div>
            <p v-if="heroSmsCurrentCountryLabel()" class="mt-2 text-xs text-slate-400">
              当前选择: {{ heroSmsCurrentCountryLabel() }}
            </p>
            <p v-if="heroSmsCountriesError" class="mt-2 text-xs text-red-400">
              {{ heroSmsCountriesError }}
            </p>
            <div
              v-if="heroSmsCountryDropdownOpen && heroSmsCountries.length"
              class="mt-3 rounded-2xl border border-white/10 bg-slate-950/40 p-3"
            >
              <input
                v-model="heroSmsCountrySearch"
                type="text"
                placeholder="按 ID / 中英俄文名搜索"
                class="input-dark"
              />
              <div class="mt-2 max-h-64 overflow-y-auto divide-y divide-white/5">
                <button
                  v-for="country in filteredHeroSmsCountries"
                  :key="country.id"
                  type="button"
                  class="flex w-full items-center justify-between gap-3 px-2 py-2 text-left text-sm text-slate-200 hover:bg-white/5"
                  @click="selectHeroSmsCountry(country)"
                >
                  <span class="truncate">{{ country.chn || country.eng || country.rus || `id=${country.id}` }}</span>
                  <span class="font-mono text-xs text-slate-400">id={{ country.id }}</span>
                </button>
                <div v-if="!filteredHeroSmsCountries.length" class="px-2 py-2 text-xs text-slate-500">
                  没有匹配结果
                </div>
              </div>
            </div>

            <!-- 实时号库面板 -->
            <div
              v-if="heroSmsAvailabilityOpen"
              class="mt-3 rounded-2xl border border-emerald-400/20 bg-emerald-500/5 p-3"
            >
              <div class="flex items-center justify-between gap-2">
                <div class="text-xs text-slate-300">
                  服务 <span class="font-mono text-emerald-200">{{ heroSmsAvailabilityService }}</span> 当前实时库存
                  (按数量排序;count=0 已过滤)
                </div>
                <button
                  type="button"
                  class="text-xs text-slate-400 hover:text-slate-200"
                  @click="heroSmsAvailabilityOpen = false"
                >
                  收起
                </button>
              </div>
              <p v-if="heroSmsAvailabilityError" class="mt-2 text-xs text-red-400">
                {{ heroSmsAvailabilityError }}
              </p>
              <div
                v-if="heroSmsAvailability.length"
                class="mt-2 max-h-72 overflow-y-auto divide-y divide-white/5"
              >
                <button
                  v-for="row in heroSmsAvailability"
                  :key="row.country"
                  type="button"
                  class="grid w-full grid-cols-[80px_1fr_auto_auto] items-center gap-3 px-2 py-2 text-left text-sm text-slate-200 hover:bg-white/5"
                  @click="runtimeForm.HERO_SMS_COUNTRY = String(row.country); heroSmsAvailabilityOpen = false"
                >
                  <span class="font-mono text-xs text-slate-400">id={{ row.country }}</span>
                  <span class="truncate">{{ heroSmsCountryNameById(row.country) || `country=${row.country}` }}</span>
                  <span class="text-xs text-emerald-200">{{ row.count.toLocaleString() }} 个</span>
                  <span class="text-xs text-slate-400">${{ row.cost }}</span>
                </button>
              </div>
              <p v-else-if="!heroSmsAvailabilityError" class="mt-2 text-xs text-slate-400">
                没有可用国家,请稍后再试或换其他服务代码
              </p>
              <p class="mt-2 text-[11px] leading-5 text-slate-500">
                提示:网站上看到的"国家列表"只是平台支持的 200+ 个国家,与号库无关。这里展示的是 SMS-Activate 协议
                getPrices 的实时数据,只显示当前真有号可买的国家。点击行即可写入"国家"字段。
              </p>
            </div>
          </div>

          <!-- 服务:可搜索下拉 + 文本兜底 -->
          <div class="rounded-2xl border border-white/10 bg-white/5 p-4 md:col-span-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <label class="block text-sm font-medium text-slate-300">
                {{ fieldByKey('HERO_SMS_SERVICE')?.prompt || '服务' }}
              </label>
              <button
                type="button"
                class="btn-secondary text-xs"
                :disabled="heroSmsServicesLoading"
                @click="loadHeroSmsServices"
              >
                {{ heroSmsServicesLoading
                    ? '加载中...'
                    : (heroSmsServices.length
                        ? `刷新列表（已加载 ${heroSmsServices.length}）`
                        : '从 HeroSMS 加载服务列表') }}
              </button>
            </div>
            <div class="mt-3 flex flex-wrap items-center gap-3">
              <input
                v-model="runtimeForm.HERO_SMS_SERVICE"
                type="text"
                placeholder="服务代码,hero-sms 上 OpenAI = dr"
                autocomplete="off"
                name="runtime-HERO_SMS_SERVICE"
                class="input-dark flex-1 min-w-[180px]"
              />
              <button
                type="button"
                class="btn-secondary text-xs"
                :disabled="!heroSmsServices.length"
                @click="heroSmsServiceDropdownOpen = !heroSmsServiceDropdownOpen"
              >
                {{ heroSmsServiceDropdownOpen ? '收起选择器' : '展开搜索选择器' }}
              </button>
            </div>
            <p v-if="heroSmsCurrentServiceLabel()" class="mt-2 text-xs text-slate-400">
              当前选择: {{ heroSmsCurrentServiceLabel() }}
            </p>
            <p v-if="heroSmsServicesError" class="mt-2 text-xs text-red-400">
              {{ heroSmsServicesError }}
            </p>
            <div
              v-if="heroSmsServiceDropdownOpen && heroSmsServices.length"
              class="mt-3 rounded-2xl border border-white/10 bg-slate-950/40 p-3"
            >
              <input
                v-model="heroSmsServiceSearch"
                type="text"
                placeholder="按代码或名称搜索(中文已切换)"
                class="input-dark"
              />
              <div class="mt-2 max-h-64 overflow-y-auto divide-y divide-white/5">
                <button
                  v-for="service in filteredHeroSmsServices"
                  :key="service.code"
                  type="button"
                  class="flex w-full items-center justify-between gap-3 px-2 py-2 text-left text-sm text-slate-200 hover:bg-white/5"
                  @click="selectHeroSmsService(service)"
                >
                  <span class="truncate">{{ service.name || service.code }}</span>
                  <span class="font-mono text-xs text-slate-400">{{ service.code }}</span>
                </button>
                <div v-if="!filteredHeroSmsServices.length" class="px-2 py-2 text-xs text-slate-500">
                  没有匹配结果
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 lg:flex-row lg:items-center lg:justify-between">
          <p class="text-xs leading-6 text-slate-400">
            {{ currentRuntimeCategoryMeta?.footer || '保存后立即热加载;清空 API Key 即视为关闭。' }}
          </p>
          <button
            @click="saveRuntimeConfig"
            :disabled="runtimeSaving || runtimeLoading"
            class="btn-primary"
          >
            {{ runtimeSaving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </div>

      <div v-else class="space-y-4">
        <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          <div v-for="field in currentRuntimeFields" :key="field.key" class="rounded-2xl border border-white/10 bg-white/5 p-4">
            <label class="mb-2 block text-sm font-medium text-slate-300">
              {{ field.prompt }}
              <span v-if="isRuntimeRequired(field)" class="text-red-400">*</span>
              <span v-if="field.key === 'API_KEY'" class="ml-1 text-xs text-slate-500">（留空自动生成）</span>
            </label>
            <input
              v-model="runtimeForm[field.key]"
              :type="fieldInputType(field.key)"
              :placeholder="field.default || ''"
              :autocomplete="fieldAutocomplete(field.key)"
              :name="`runtime-${field.key}`"
              class="input-dark"
            />
          </div>
        </div>

        <div class="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 lg:flex-row lg:items-center lg:justify-between">
          <p class="text-xs leading-6 text-slate-400">
            {{ currentRuntimeCategoryMeta?.footer }}
          </p>
          <button
            @click="saveRuntimeConfig"
            :disabled="runtimeSaving || runtimeLoading"
            class="btn-primary"
          >
            {{ runtimeSaving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </div>
    </div>

    <Settings
      v-else-if="visualCategory === 'admin'"
      :admin-status="adminStatus"
      :codex-status="codexStatus"
      section="admin"
      @refresh="$emit('refresh')"
      @admin-progress="$emit('admin-progress')"
    />

    <Settings
      v-else-if="visualCategory === 'auto-check'"
      :admin-status="adminStatus"
      :codex-status="codexStatus"
      section="auto-check"
      @refresh="$emit('refresh')"
      @admin-progress="$emit('admin-progress')"
    />

    <div v-else-if="visualCategory === 'source'" class="glass-card space-y-4 p-6">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div class="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">
            <span>📝</span>
            Source Editor
          </div>
          <h3 class="section-heading">源文件编辑</h3>
          <p class="section-subtitle">
            直接编辑 .env 源文件。保存后会立即重载并校验邮箱服务 / 远端同步配置。
          </p>
        </div>
        <div class="status-badge break-all font-mono text-[11px] text-slate-400">
          {{ sourcePath || '.env' }}
        </div>
      </div>

      <div
        v-if="sourceMessage"
        class="rounded-2xl px-4 py-3 text-sm border"
        :class="sourceMessageClass"
      >
        {{ sourceMessage }}
      </div>

      <textarea
        v-model="sourceContent"
        rows="20"
        spellcheck="false"
        class="textarea-dark min-h-[420px] font-mono"
        placeholder="在这里编辑 .env 内容"
      />

      <div class="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 lg:flex-row lg:items-center lg:justify-between">
        <p class="text-xs leading-6 text-slate-400">
          这里是原始文本模式，适合你直接粘贴或手工维护完整 .env。
        </p>
        <div class="flex gap-2">
          <button
            @click="loadSourceConfig"
            :disabled="sourceLoading || sourceSaving"
            class="btn-secondary"
          >
            {{ sourceLoading ? '加载中...' : '重新读取' }}
          </button>
          <button
            @click="saveSourceConfig"
            :disabled="sourceLoading || sourceSaving"
            class="btn-primary"
          >
            {{ sourceSaving ? '保存中...' : '保存源文件' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, setApiKey } from '../api.js'
import Settings from './Settings.vue'

defineProps({
  adminStatus: {
    type: Object,
    default: null,
  },
  codexStatus: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['refresh', 'admin-progress'])

const runtimeCategoryKeys = {
  cloudmail: ['MAIL_PROVIDER', 'CLOUDMAIL_BASE_URL', 'CLOUDMAIL_EMAIL', 'CLOUDMAIL_PASSWORD', 'CLOUDMAIL_DOMAIN', 'CF_TEMP_EMAIL_BASE_URL', 'CF_TEMP_EMAIL_ADMIN_PASSWORD', 'CF_TEMP_EMAIL_DOMAIN'],
  sync: [
    'SYNC_TARGET_SUB2API',
    'SUB2API_URL',
    'SUB2API_EMAIL',
    'SUB2API_PASSWORD',
    'SUB2API_GROUP',
    'SUB2API_CONCURRENCY',
    'SUB2API_PRIORITY',
    'SUB2API_RATE_MULTIPLIER',
    'SUB2API_AUTO_PAUSE_ON_EXPIRED',
    'SUB2API_MODEL_WHITELIST',
    'SUB2API_OPENAI_WS_MODE',
    'SUB2API_OPENAI_PASSTHROUGH',
    'SUB2API_OVERWRITE_ACCOUNT_SETTINGS',
    'SUB2API_PROXY',
  ],
  proxy: ['PLAYWRIGHT_PROXY_URL', 'PLAYWRIGHT_PROXY_BYPASS'],
  security: ['API_KEY'],
  hero_sms: [
    'HERO_SMS_API_KEY',
    'HERO_SMS_SERVICE',
    'HERO_SMS_COUNTRY',
    'HERO_SMS_OPERATOR',
    'HERO_SMS_MAX_PRICE',
    'HERO_SMS_PHONE_REUSE_MAX',
    'HERO_SMS_FORCE_NEW_PHONE',
    'HERO_SMS_WAIT_SECONDS',
    'HERO_SMS_HTTP_TIMEOUT',
    'HERO_SMS_BASE_URL',
  ],
}

const runtimeCategoryMeta = {
  cloudmail: {
    icon: '📧',
    badge: 'Mail Provider',
    title: '邮箱服务配置',
    description: '配置自动注册和收验证码所需的邮箱后端。可以在 CloudMail 和 Cloudflare Temp Email 之间切换。',
    note: '带 * 的项会直接影响账号池操作；只有当前选中的邮箱提供者配置会被视为运行时必填。',
    footer: '邮箱提供者配置保存后会立即热加载；之后的注册、复用和验证码轮询会直接使用新配置。',
  },
  sync: {
    icon: '☁️',
    badge: 'Remote Sync',
    title: '远端同步',
    description: '先选择启用的远端同步目标，再填写对应的连接信息。账号池操作会根据这里的启用状态决定同步到哪些远端。',
    note: '当前仅支持 Sub2API 同步目标；启用并填写对应配置即可。',
  },
  proxy: {
    icon: '🛰️',
    badge: 'Proxy / Advanced',
    title: '代理 / 高级',
    description: '用于单独配置 Playwright 浏览器流量代理。属于低频项，默认折叠，避免把主配置界面堆得过满。',
    note: '只有在代理 ChatGPT / Auth 页面访问时才建议配置；本地回调场景通常还需要设置 bypass。',
  },
  security: {
    icon: '🔐',
    badge: 'Security',
    title: '安全 / 访问控制',
    description: '入口级配置集中放在这里。API Key 决定 Web 面板和 HTTP API 的访问控制，不再和其他运行参数混在一起。',
    note: '留空会自动生成新的 API Key；保存后前端会立即切换到新的密钥。',
    footer: '这是控制面板和 API 的入口密钥。修改后会立即生效，并同步刷新当前浏览器里的 API Key。',
  },
  hero_sms: {
    icon: '📱',
    badge: 'SMS Activate',
    title: '接码服务 (HeroSMS)',
    description: 'OAuth 流程偶尔会被 OpenAI 推到手机号验证页。配置 HeroSMS 后,登录器会通过 OpenAI 官方 HTTP API 自动完成 add-phone(send → SMS → validate),期间精准识别号码上限/VoIP 拒绝/OTP 错;同一号码在 20 分钟内最多复用 N 次。',
    note: 'OpenAI 在 hero-sms 上的服务代码是 dr(默认值);国家 ID 是 hero-sms 自家编号,与 SMS-Activate 标准不同(187=USA、0=Russia、3=China)。可点"加载列表"搜索具体国家。',
    footer: '保存后立即热加载;清空 API Key 即视为关闭。',
  },
}

const visualCategories = [
  { key: 'cloudmail', label: '邮箱服务', icon: '📧' },
  { key: 'sync', label: '远端同步', icon: '☁️' },
  { key: 'hero_sms', label: '接码服务', icon: '📱' },
  { key: 'security', label: '安全 / 访问控制', icon: '🔐' },
  { key: 'admin', label: '管理员 / 主号', icon: '👤' },
  { key: 'auto-check', label: '巡检设置', icon: '🔄' },
  { key: 'source', label: '源文件编辑', icon: '📝' },
  { key: 'proxy', label: '代理 / 高级', icon: '🛰️' },
]

const visualCategory = ref('cloudmail')
const proxyExpanded = ref(false)

const runtimeFields = ref([])
const runtimeForm = reactive({})
const runtimeLoading = ref(false)
const runtimeSaving = ref(false)
const runtimeSaved = ref(false)
const runtimeMessage = ref('')
const runtimeMessageClass = ref('')

const sourcePath = ref('')
const sourceContent = ref('')
const sourceLoading = ref(false)
const sourceSaving = ref(false)
const sourceLoaded = ref(false)
const sourceMessage = ref('')
const sourceMessageClass = ref('')
const runtimeRequiredKeys = new Set(['API_KEY'])
const sub2apiFieldHints = {
  SUB2API_URL: 'ENV: SUB2API_URL · Sub2API API base URL',
  SUB2API_EMAIL: 'ENV: SUB2API_EMAIL · login.email',
  SUB2API_PASSWORD: 'ENV: SUB2API_PASSWORD · login.password',
  SUB2API_GROUP: 'ENV: SUB2API_GROUP · group_ids',
  SUB2API_PROXY: 'ENV: SUB2API_PROXY · account.proxy_id（ID 或名称，仅账号池新建时写入）',
  SUB2API_CONCURRENCY: 'ENV: SUB2API_CONCURRENCY · account.concurrency',
  SUB2API_PRIORITY: 'ENV: SUB2API_PRIORITY · account.priority',
  SUB2API_RATE_MULTIPLIER: 'ENV: SUB2API_RATE_MULTIPLIER · account.rate_multiplier',
  SUB2API_AUTO_PAUSE_ON_EXPIRED: 'ENV: SUB2API_AUTO_PAUSE_ON_EXPIRED · account.auto_pause_on_expired',
  SUB2API_MODEL_WHITELIST: 'ENV: SUB2API_MODEL_WHITELIST · credentials.model_mapping',
  SUB2API_OPENAI_WS_MODE: 'ENV: SUB2API_OPENAI_WS_MODE · extra.openai_oauth_responses_websockets_v2_mode / enabled',
  SUB2API_OPENAI_PASSTHROUGH: 'ENV: SUB2API_OPENAI_PASSTHROUGH · extra.openai_passthrough',
  SUB2API_OVERWRITE_ACCOUNT_SETTINGS: 'ENV: SUB2API_OVERWRITE_ACCOUNT_SETTINGS · AutoTeam overwrite switch',
}

const selectedRuntimeCategory = computed(() => runtimeCategoryKeys[visualCategory.value] ? visualCategory.value : '')
const currentRuntimeCategoryMeta = computed(() => runtimeCategoryMeta[selectedRuntimeCategory.value] || null)

function fieldByKey(key) {
  return runtimeFields.value.find(field => field.key === key) || null
}

function sub2apiFieldHint(key) {
  return sub2apiFieldHints[key] || ''
}

function fieldsByKeys(keys) {
  return keys
    .map(key => fieldByKey(key))
    .filter(Boolean)
}

const securityFields = computed(() => fieldsByKeys(runtimeCategoryKeys.security))
const proxyFields = computed(() => fieldsByKeys(runtimeCategoryKeys.proxy))
const heroSmsFields = computed(() => fieldsByKeys(runtimeCategoryKeys.hero_sms))
const heroSmsApiKeyConfigured = computed(() => Boolean(String(runtimeForm.HERO_SMS_API_KEY || '').trim()))

// HeroSMS 元数据(国家/服务)的远端缓存与搜索状态。在面板内做"可搜索下拉",
// 同时保留输入框允许用户手填官网新增的国家/服务码。
const heroSmsCountries = ref([])
const heroSmsCountriesLoading = ref(false)
const heroSmsCountriesError = ref('')
const heroSmsCountrySearch = ref('')
const heroSmsCountryDropdownOpen = ref(false)

const heroSmsServices = ref([])
const heroSmsServicesLoading = ref(false)
const heroSmsServicesError = ref('')
const heroSmsServiceSearch = ref('')
const heroSmsServiceDropdownOpen = ref(false)

// 实时按服务查可用国家(走 SMS-Activate getPrices)。这是唯一可靠的"哪些国家此刻有号"
// 数据来源,网站上看到的国家列表只是平台支持的国家,跟库存无关。
const heroSmsAvailability = ref([])
const heroSmsAvailabilityLoading = ref(false)
const heroSmsAvailabilityError = ref('')
const heroSmsAvailabilityOpen = ref(false)
const heroSmsAvailabilityService = ref('')

function _normalizeText(value) {
  return String(value ?? '').toLowerCase()
}

const filteredHeroSmsCountries = computed(() => {
  const q = _normalizeText(heroSmsCountrySearch.value).trim()
  const all = heroSmsCountries.value
  if (!q) return all.slice(0, 200)
  return all.filter((c) => {
    const idText = String(c.id ?? '')
    return (
      idText === q ||
      idText.includes(q) ||
      _normalizeText(c.eng).includes(q) ||
      _normalizeText(c.rus).includes(q) ||
      _normalizeText(c.chn).includes(q)
    )
  }).slice(0, 200)
})

const filteredHeroSmsServices = computed(() => {
  const q = _normalizeText(heroSmsServiceSearch.value).trim()
  const all = heroSmsServices.value
  if (!q) return all.slice(0, 200)
  return all.filter((s) => {
    return (
      _normalizeText(s.code).includes(q) ||
      _normalizeText(s.name).includes(q)
    )
  }).slice(0, 200)
})

function heroSmsCountryLabel(country) {
  if (!country) return ''
  const name = country.chn || country.eng || country.rus || ''
  return name ? `${name} (id=${country.id})` : `id=${country.id}`
}

function heroSmsCurrentCountryLabel() {
  const id = String(runtimeForm.HERO_SMS_COUNTRY ?? '').trim()
  if (!id) return ''
  const matched = heroSmsCountries.value.find((c) => String(c.id) === id)
  return matched ? heroSmsCountryLabel(matched) : ''
}

function heroSmsServiceLabel(service) {
  if (!service) return ''
  return service.name ? `${service.name} (${service.code})` : service.code
}

function heroSmsCurrentServiceLabel() {
  const code = String(runtimeForm.HERO_SMS_SERVICE ?? '').trim().toLowerCase()
  if (!code) return ''
  const matched = heroSmsServices.value.find((s) => String(s.code).toLowerCase() === code)
  return matched ? heroSmsServiceLabel(matched) : ''
}

async function loadHeroSmsCountries() {
  heroSmsCountriesError.value = ''
  heroSmsCountriesLoading.value = true
  try {
    const payload = {
      apiKey: String(runtimeForm.HERO_SMS_API_KEY || '').trim(),
      baseUrl: String(runtimeForm.HERO_SMS_BASE_URL || '').trim(),
    }
    const resp = await api.getHeroSmsCountries(payload)
    const list = Array.isArray(resp?.data) ? resp.data : []
    // 平台返回 visible=0 的国家通常代表停售;过滤掉避免误选
    heroSmsCountries.value = list.filter((c) => c && c.visible !== 0)
    if (!heroSmsCountries.value.length) {
      heroSmsCountriesError.value = '平台未返回国家数据'
    }
  } catch (err) {
    heroSmsCountriesError.value = err?.message || '加载国家列表失败'
    heroSmsCountries.value = []
  } finally {
    heroSmsCountriesLoading.value = false
  }
}

async function loadHeroSmsServices() {
  heroSmsServicesError.value = ''
  heroSmsServicesLoading.value = true
  try {
    const payload = {
      apiKey: String(runtimeForm.HERO_SMS_API_KEY || '').trim(),
      baseUrl: String(runtimeForm.HERO_SMS_BASE_URL || '').trim(),
      country: String(runtimeForm.HERO_SMS_COUNTRY || '').trim(),
      lang: 'cn',
    }
    const resp = await api.getHeroSmsServices(payload)
    const list = Array.isArray(resp?.data) ? resp.data : []
    heroSmsServices.value = list
    if (!list.length) {
      heroSmsServicesError.value = '平台未返回服务数据'
    }
  } catch (err) {
    heroSmsServicesError.value = err?.message || '加载服务列表失败'
    heroSmsServices.value = []
  } finally {
    heroSmsServicesLoading.value = false
  }
}

async function loadHeroSmsAvailability() {
  heroSmsAvailabilityError.value = ''
  heroSmsAvailabilityLoading.value = true
  try {
    const service = String(runtimeForm.HERO_SMS_SERVICE || 'dr').trim() || 'dr'
    const payload = {
      apiKey: String(runtimeForm.HERO_SMS_API_KEY || '').trim(),
      baseUrl: String(runtimeForm.HERO_SMS_BASE_URL || '').trim(),
      service,
      limit: 50,
    }
    const resp = await api.getHeroSmsAvailability(payload)
    heroSmsAvailability.value = Array.isArray(resp?.data) ? resp.data : []
    heroSmsAvailabilityService.value = service
    heroSmsAvailabilityOpen.value = true
    if (!heroSmsAvailability.value.length) {
      heroSmsAvailabilityError.value = `服务 ${service} 当前在所有国家都没有库存(NO_NUMBERS)`
    }
  } catch (err) {
    heroSmsAvailabilityError.value = err?.message || '查询实时号库失败'
    heroSmsAvailability.value = []
  } finally {
    heroSmsAvailabilityLoading.value = false
  }
}

function heroSmsCountryNameById(id) {
  if (id === undefined || id === null) return ''
  const matched = heroSmsCountries.value.find((c) => String(c.id) === String(id))
  if (!matched) return ''
  return matched.chn || matched.eng || matched.rus || `id=${id}`
}

function selectHeroSmsCountry(country) {
  if (!country) return
  runtimeForm.HERO_SMS_COUNTRY = String(country.id)
  heroSmsCountrySearch.value = ''
  heroSmsCountryDropdownOpen.value = false
}

function selectHeroSmsService(service) {
  if (!service) return
  runtimeForm.HERO_SMS_SERVICE = String(service.code)
  heroSmsServiceSearch.value = ''
  heroSmsServiceDropdownOpen.value = false
}
const syncToggleFields = computed(() => fieldsByKeys(['SYNC_TARGET_SUB2API']))
const selectedMailProvider = computed(() => String(runtimeForm.MAIL_PROVIDER || 'cloudmail').toLowerCase() === 'cloudflare_temp_email' ? 'cloudflare_temp_email' : 'cloudmail')
const cloudmailProviderFields = computed(() => fieldsByKeys(['CLOUDMAIL_BASE_URL', 'CLOUDMAIL_EMAIL', 'CLOUDMAIL_PASSWORD', 'CLOUDMAIL_DOMAIN']))
const cfTempEmailFields = computed(() => fieldsByKeys(['CF_TEMP_EMAIL_BASE_URL', 'CF_TEMP_EMAIL_ADMIN_PASSWORD', 'CF_TEMP_EMAIL_DOMAIN']))

const syncSub2apiEnabled = computed(() => String(runtimeForm.SYNC_TARGET_SUB2API || '').toLowerCase() === 'true')
const syncSub2apiConnectionFields = computed(() => syncSub2apiEnabled.value
  ? fieldsByKeys(['SUB2API_URL', 'SUB2API_EMAIL', 'SUB2API_PASSWORD', 'SUB2API_GROUP'])
  : [])
const syncSub2apiDefaultFields = computed(() => syncSub2apiEnabled.value
  ? fieldsByKeys([
      'SUB2API_CONCURRENCY',
      'SUB2API_PRIORITY',
      'SUB2API_RATE_MULTIPLIER',
      'SUB2API_AUTO_PAUSE_ON_EXPIRED',
      'SUB2API_MODEL_WHITELIST',
      'SUB2API_OPENAI_WS_MODE',
      'SUB2API_OPENAI_PASSTHROUGH',
      'SUB2API_OVERWRITE_ACCOUNT_SETTINGS',
      'SUB2API_PROXY',
    ])
  : [])

const currentRuntimeFields = computed(() => {
  if (selectedRuntimeCategory.value === 'cloudmail') {
    return selectedMailProvider.value === 'cloudflare_temp_email'
      ? cfTempEmailFields.value
      : cloudmailProviderFields.value
  }
  if (selectedRuntimeCategory.value === 'security') {
    return securityFields.value
  }
  if (selectedRuntimeCategory.value === 'hero_sms') {
    return heroSmsFields.value
  }
  return []
})

const enabledSyncTargetsText = computed(() => {
  const targets = []
  if (syncSub2apiEnabled.value) {
    targets.push('Sub2API')
  }
  return targets.length ? `已启用：${targets.join(' + ')}` : '当前未启用远端'
})

const currentRuntimeStatus = computed(() => {
  if (!selectedRuntimeCategory.value) {
    return {
      label: '',
      class: 'border-white/10 bg-white/5 text-slate-400',
    }
  }

  if (selectedRuntimeCategory.value === 'sync') {
    if (!syncSub2apiEnabled.value) {
      return {
        label: '未启用',
        class: 'border-white/10 bg-white/5 text-slate-400',
      }
    }

    const sub2apiReady = !syncSub2apiEnabled.value || syncSub2apiConnectionFields.value.every(field => !isRuntimeRequired(field) || field.configured)

    return sub2apiReady
      ? {
          label: '已配置',
          class: 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200',
        }
      : {
          label: '未配置',
          class: 'border-red-400/20 bg-red-500/10 text-red-200',
        }
  }

  if (selectedRuntimeCategory.value === 'proxy') {
    return proxyFields.value.some(field => field.configured)
      ? {
          label: '已设置',
          class: 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200',
        }
      : {
          label: '未设置',
          class: 'border-white/10 bg-white/5 text-slate-400',
        }
  }

  if (selectedRuntimeCategory.value === 'cloudmail') {
    const providerFields = selectedMailProvider.value === 'cloudflare_temp_email'
      ? cfTempEmailFields.value
      : cloudmailProviderFields.value
    const configured = providerFields.length > 0 && providerFields.every(field => !isRuntimeRequired(field) || field.configured)
    return configured
      ? {
          label: '已配置',
          class: 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200',
        }
      : {
          label: '未配置',
          class: 'border-red-400/20 bg-red-500/10 text-red-200',
        }
  }

  if (selectedRuntimeCategory.value === 'hero_sms') {
    return heroSmsApiKeyConfigured.value
      ? {
          label: '已启用',
          class: 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200',
        }
      : {
          label: '未启用',
          class: 'border-white/10 bg-white/5 text-slate-400',
        }
  }

  const fields = currentRuntimeFields.value
  const configured = fields.length > 0 && fields.every(field => !isRuntimeRequired(field) || field.configured)

  return configured
    ? {
        label: '已配置',
        class: 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200',
      }
    : {
        label: '未配置',
        class: 'border-red-400/20 bg-red-500/10 text-red-200',
      }
})

function setRuntimeMessage(text, type = 'success') {
  runtimeMessage.value = text
  runtimeMessageClass.value = type === 'success'
    ? 'bg-green-500/10 text-green-400 border-green-500/20'
    : 'bg-red-500/10 text-red-400 border-red-500/20'
  window.clearTimeout(setRuntimeMessage._timer)
  setRuntimeMessage._timer = window.setTimeout(() => {
    runtimeMessage.value = ''
  }, 8000)
}

function setSourceMessage(text, type = 'success') {
  sourceMessage.value = text
  sourceMessageClass.value = type === 'success'
    ? 'bg-green-500/10 text-green-400 border-green-500/20'
    : 'bg-red-500/10 text-red-400 border-red-500/20'
  window.clearTimeout(setSourceMessage._timer)
  setSourceMessage._timer = window.setTimeout(() => {
    sourceMessage.value = ''
  }, 8000)
}

function fieldInputType(key) {
  if ([
    'SUB2API_CONCURRENCY',
    'SUB2API_PRIORITY',
    'SUB2API_RATE_MULTIPLIER',
    'HERO_SMS_MAX_PRICE',
    'HERO_SMS_HTTP_TIMEOUT',
    'HERO_SMS_WAIT_SECONDS',
    'HERO_SMS_PHONE_REUSE_MAX',
  ].includes(key)) {
    return 'number'
  }
  return key.includes('PASSWORD') || key.includes('KEY') ? 'password' : 'text'
}

function fieldAutocomplete(key) {
  // password 字段加 new-password 阻止浏览器把其他网站存的密码自动填充进来,
  // 否则 v-model 会读到错误的值并被悄悄保存到 .env
  return fieldInputType(key) === 'password' ? 'new-password' : 'off'
}

function isToggleField(key) {
  return key === 'SYNC_TARGET_SUB2API'
}

function isBooleanStringField(key) {
  return isToggleField(key) || [
    'SUB2API_AUTO_PAUSE_ON_EXPIRED',
    'SUB2API_OPENAI_PASSTHROUGH',
    'SUB2API_OVERWRITE_ACCOUNT_SETTINGS',
  ].includes(key)
}

function isWsModeField(key) {
  return key === 'SUB2API_OPENAI_WS_MODE'
}

function fieldInputStep(key) {
  if (key === 'SUB2API_RATE_MULTIPLIER') {
    return '0.001'
  }
  if (key === 'SUB2API_CONCURRENCY' || key === 'SUB2API_PRIORITY') {
    return '1'
  }
  return undefined
}

function isRuntimeRequired(field) {
  return Boolean(field?.runtime_required) || runtimeRequiredKeys.has(field?.key)
}

function normalizeRuntimeFieldValue(field) {
  const value = field?.value ?? field?.default ?? ''
  if (isBooleanStringField(field?.key)) {
    return String(value).toLowerCase() === 'true' ? 'true' : 'false'
  }
  if (isWsModeField(field?.key)) {
    const mode = String(value || '').toLowerCase()
    return ['off', 'ctx_pool', 'passthrough'].includes(mode) ? mode : 'off'
  }
  return value
}

async function loadRuntimeConfig() {
  runtimeLoading.value = true
  try {
    const result = await api.getRuntimeConfig()
    runtimeFields.value = result.fields || []

    for (const key of Object.keys(runtimeForm)) {
      if (!runtimeFields.value.find(field => field.key === key)) {
        delete runtimeForm[key]
      }
    }
    for (const field of runtimeFields.value) {
      runtimeForm[field.key] = normalizeRuntimeFieldValue(field)
    }
  } catch (e) {
    console.error('加载运行时配置失败:', e)
    setRuntimeMessage('加载运行时配置失败: ' + e.message, 'error')
  } finally {
    runtimeLoading.value = false
  }
}

async function saveRuntimeConfig() {
  runtimeSaving.value = true
  runtimeSaved.value = false
  try {
    const payload = {}
    for (const field of runtimeFields.value) {
      const value = runtimeForm[field.key]
      payload[field.key] = value == null ? '' : String(value)
    }
    const result = await api.saveRuntimeConfig(payload)
    if (result.api_key) {
      setApiKey(result.api_key)
    }
    setRuntimeMessage(result.message || '配置保存成功')
    runtimeSaved.value = true
    window.setTimeout(() => {
      runtimeSaved.value = false
    }, 3000)
    await loadRuntimeConfig()
    emit('refresh')
  } catch (e) {
    setRuntimeMessage(e.message, 'error')
  } finally {
    runtimeSaving.value = false
  }
}

async function loadSourceConfig() {
  sourceLoading.value = true
  try {
    const result = await api.getRuntimeConfigSource()
    sourcePath.value = result.path || '.env'
    sourceContent.value = result.content || ''
    sourceLoaded.value = true
  } catch (e) {
    console.error('加载源文件失败:', e)
    setSourceMessage('加载源文件失败: ' + e.message, 'error')
  } finally {
    sourceLoading.value = false
  }
}

async function saveSourceConfig() {
  sourceSaving.value = true
  try {
    const result = await api.saveRuntimeConfigSource({ content: sourceContent.value })
    if (result.api_key) {
      setApiKey(result.api_key)
    }
    setSourceMessage(result.message || '源文件保存成功')
    await Promise.all([loadSourceConfig(), loadRuntimeConfig()])
    emit('refresh')
  } catch (e) {
    setSourceMessage(e.message, 'error')
  } finally {
    sourceSaving.value = false
  }
}

watch(visualCategory, async (next) => {
  if (next === 'source' && !sourceLoaded.value) {
    await loadSourceConfig()
  }
  // 切到接码服务且已配置 API Key,自动拉一次国家列表减少手动操作
  if (next === 'hero_sms' && heroSmsApiKeyConfigured.value && !heroSmsCountries.value.length) {
    loadHeroSmsCountries()
  }
})

// 国家变化时,已加载的服务列表对应的国家就过期了,清空让用户重新拉
watch(() => runtimeForm.HERO_SMS_COUNTRY, (next, prev) => {
  if (next !== prev && heroSmsServices.value.length) {
    heroSmsServices.value = []
    heroSmsServiceDropdownOpen.value = false
  }
})

onMounted(async () => {
  await loadRuntimeConfig()
})
</script>
