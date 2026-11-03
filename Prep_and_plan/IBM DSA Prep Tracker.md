# IBM DSA Prep Tracker

250 problems · tick to track · notes and progress save in this browser

**0 / 250**0%

Each problem has Google and YouTube search links that open in a new tab. Progress is stored only in this browser, so clearing site data resets it.

## 1. How to follow this plan

1. Go topic by topic in the order of the 8-week plan, starting with the **IBM-Reported Must-Do Set**.
2. Per problem: **20 min** thinking, then a hint or the editorial, then close it and re-code from memory the next day.
3. Aim for **3 to 4 problems a day**: 2 new, 1 re-solve, 1 timed.
4. Every Sunday, re-solve anything you needed help on and take one **50-minute, 2-problem mock** (that is IBM’s OA format).
5. Easy and Medium first. Skip Hard problems until the rest of a topic is done.

## 2. How to approach any problem

1. **Restate** the problem and ask about input size, duplicates, negatives and empty input.
2. Give a **brute force** and its complexity out loud.
3. Find the bottleneck, then name the pattern (hashing, two pointers, window, sort, heap, DP).
4. Write the optimal approach in 2 lines before coding.
5. Code cleanly, then **dry-run** on a small input and one edge case.
6. State time and space complexity, then mention one alternative.

## 3. How to take notes

Keep **one note per problem**, in the Notes box under that problem. Keep it short enough to re-read in 60 seconds. Use this template (the Notes button can insert it):

```
Pattern:
Key insight (one line):
Brute force + complexity:
Optimal + complexity:
Edge cases:
Mistake I made:
Similar problems:
Re-solve on (date):
```

### What to remember after each problem

- The **trigger**: what in the statement tells you which pattern to use (“sorted array” → binary search or two pointers, “top K” → heap, “count ways” → DP).
- The **one trick** that unlocked it, in a single sentence.
- The **bug** you wrote (off-by-one, overflow, not resetting a map). Your bug list is the best revision sheet before the OA.
- The complexity and **why**, not just the numbers.
- Which 2 other problems use the same pattern.

Before the OA, read only your **“Mistake I made”** lines and the **Key insight** lines. Do not re-read full solutions.

## 4. How IBM asks questions

- **Online assessment:** 2 coding problems on HackerRank (about 50 to 70 minutes), easy to medium, mostly arrays and strings, followed by an English test. Some campus drives add a group discussion.
- **Interview:** your **projects first**, then OOPs, DBMS (keys, joins, writing SQL), basic DSA and a few core CS concepts. Reported DSA topics: palindrome, reverse, binary search, bubble sort, target sum, kth max with a priority queue, permutations, trees.
- **Follow-ups:** “Can you reduce time or space?” So practise going from brute force to optimal out loud.
- **Behavioural:** expect “why IBM”, handling being stuck, and teamwork. Prepare 3 short stories from your projects.
- Formats change by year and role, so confirm the pattern with your placement cell.

## 5. How other companies ask (typical patterns)

| Type | Typical style | Focus |
| --- | --- | --- |
| IBM, TCS, Infosys, Wipro, Cognizant | OA with 1 to 3 easy/medium problems plus aptitude and English; short technical + HR | Arrays, strings, basics, projects, CS fundamentals |
| Amazon | Online assessment, then rounds mixing 1 to 2 coding problems with Leadership Principle stories | Arrays, hashing, trees, graphs, heaps, design basics; behavioural is weighted heavily |
| Microsoft | Medium problems, discussion-heavy, follow-ups and optimisation | Linked lists, trees, strings, DP basics, clean code |
| Google, Meta | Medium to hard, strong emphasis on reasoning aloud and complexity | Graphs, DP, binary search, heaps, edge cases |
| Early-stage startups | Practical: build or debug something, discuss projects deeply, 1 to 2 DSA problems | Your projects, Python/system design basics, ownership and speed |

General patterns from candidate reports and prep sites. Exact rounds vary by team and year.

## 6. Day-before and day-of checklist

- Re-read your “Mistake I made” lines for the last 2 weeks.
- Skim the IBM-Reported Must-Do Set once more.
- Use the language you are fastest in (you use C++). Know `vector`, `unordered_map`, `priority_queue`, `sort` with lambdas, and use `long long` when sums can overflow.
- In the test: read both problems first, solve the easier one fully, then the second. A passing partial is better than nothing.
- Prepare a 60-second intro and a 2-minute explanation of each project on your resume.