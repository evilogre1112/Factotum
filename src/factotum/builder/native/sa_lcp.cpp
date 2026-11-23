#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>

#ifdef _WIN32
#define API_EXPORT extern "C" __declspec(dllexport)
#else
#define API_EXPORT extern "C"
#endif

static void counting_sort_inplace(
    const std::vector<int32_t>& order,
    const std::vector<int32_t>& rank,
    int32_t classes,
    std::vector<int32_t>& out,
    std::vector<int32_t>& cnt
) {
    const int32_t n = static_cast<int32_t>(order.size());
    cnt.assign(classes, 0);
    for (int32_t idx : order) {
        cnt[rank[idx]]++;
    }
    for (int32_t i = 1; i < classes; ++i) {
        cnt[i] += cnt[i - 1];
    }
    for (int32_t i = n - 1; i >= 0; --i) {
        int32_t idx = order[i];
        int32_t val = rank[idx];
        out[--cnt[val]] = idx;
    }
}

API_EXPORT void build_sa_lcp_native(
    const int32_t* s_in,
    int32_t n,
    int32_t* sa_out,
    int32_t* lcp_out
) {
    if (n <= 0) return;
    if (n == 1) {
        sa_out[0] = 0;
        lcp_out[0] = 0;
        return;
    }

    // Coordinate compression (handles arbitrary positive & negative sentinel IDs)
    std::vector<int32_t> vals(s_in, s_in + n);
    std::sort(vals.begin(), vals.end());
    vals.erase(std::unique(vals.begin(), vals.end()), vals.end());
    int32_t classes = static_cast<int32_t>(vals.size());

    std::vector<int32_t> c(n);
    for (int32_t i = 0; i < n; ++i) {
        c[i] = static_cast<int32_t>(
            std::lower_bound(vals.begin(), vals.end(), s_in[i]) - vals.begin()
        );
    }

    std::vector<int32_t> order(n), p(n), p_new(n), c_new(n), cnt;
    for (int32_t i = 0; i < n; ++i) order[i] = i;
    counting_sort_inplace(order, c, classes, p, cnt);

    for (int32_t step = 1; step < n; step <<= 1) {
        int32_t pos = 0;
        for (int32_t i = n - step; i < n; ++i) p_new[pos++] = i;
        for (int32_t i = 0; i < n; ++i) {
            if (p[i] >= step) p_new[pos++] = p[i] - step;
        }
        counting_sort_inplace(p_new, c, classes, p, cnt);

        c_new[p[0]] = 0;
        classes = 1;
        for (int32_t i = 1; i < n; ++i) {
            int32_t cur_i = p[i];
            int32_t prv_i = p[i - 1];
            int32_t cur2 = (cur_i + step < n) ? c[cur_i + step] : -1;
            int32_t prv2 = (prv_i + step < n) ? c[prv_i + step] : -1;
            if (c[cur_i] != c[prv_i] || cur2 != prv2) {
                classes++;
            }
            c_new[cur_i] = classes - 1;
        }
        c.swap(c_new);
        if (classes == n) break;
    }

    for (int32_t i = 0; i < n; ++i) sa_out[i] = p[i];

    // Kasai O(N) LCP array
    std::vector<int32_t> rank(n);
    for (int32_t i = 0; i < n; ++i) rank[p[i]] = i;

    int32_t k = 0;
    lcp_out[0] = 0;
    for (int32_t i = 0; i < n; ++i) {
        int32_t r = rank[i];
        if (r == n - 1) {
            k = 0;
            continue;
        }
        int32_t j = p[r + 1];
        while (i + k < n && j + k < n && s_in[i + k] == s_in[j + k]) {
            ++k;
        }
        lcp_out[r + 1] = k;
        if (k > 0) --k;
    }
}

API_EXPORT int32_t extract_candidates_native(
    const int32_t* lcp,
    int32_t n,
    int32_t* out_len,
    int32_t* out_freq,
    int32_t* out_pos
) {
    struct Cand {
        int32_t len;
        int32_t freq;
        int32_t pos;
    };
    std::vector<Cand> cands;
    cands.reserve(static_cast<size_t>(n / 2 + 1));
    std::vector<std::pair<int32_t, int32_t>> st;
    st.reserve(256);

    for (int32_t i = 1; i <= n; ++i) {
        int32_t curr = (i < n) ? lcp[i] : 0;
        int32_t start = i;
        while (!st.empty() && st.back().first > curr) {
            auto [prev_len, prev_start] = st.back();
            st.pop_back();
            int32_t freq = i - prev_start + 1;
            if (prev_len > 0) {
                cands.push_back({prev_len, freq, prev_start});
            }
            start = prev_start;
        }
        if (st.empty() || st.back().first < curr) {
            st.emplace_back(curr, start);
        }
    }

    std::stable_sort(cands.begin(), cands.end(), [](const Cand& a, const Cand& b) {
        if (a.freq != b.freq) return a.freq > b.freq;
        return a.len > b.len;
    });

    const int32_t m = static_cast<int32_t>(cands.size());
    for (int32_t i = 0; i < m; ++i) {
        out_len[i] = cands[i].len;
        out_freq[i] = cands[i].freq;
        out_pos[i] = cands[i].pos;
    }
    return m;
}
