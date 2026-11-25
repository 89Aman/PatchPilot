# PatchPilot's first customer pilot

Start with a Python consultancy whose engineers already review client pull requests. Offer to sit with one reviewer for three PRs in a repository they choose. Start with a public or internal tool they can approve. The open-source app is free. A potential paid service to test is hands-on setup and support for a self-hosted deployment.

## Two conversations worth starting

| Prospect | Why reach out | Opening question |
| --- | --- | --- |
| [REVSYS](https://www.revsys.com/) | Offers Python and Django code reviews. Their reviewers could judge which inline findings earn their attention. | Would a first pass on one of your open-source PRs surface anything your reviewers would keep? |
| [Caktus Group](https://www.caktusgroup.com/) | Maintains Django applications and offers on-premises deployment. The local inference option gives the conversation a concrete starting point. | When a team needs to keep inference in-house, who would own a self-hosted review tool? |

These are research leads. Their stack, appetite for a trial and purchasing authority still need confirmation. Start with an introduction if available. REVSYS also lists a public business contact on its [contact page](https://www.revsys.com/contact/).

## Founder email draft for REVSYS

**Subject** A second pass on one Django PR?

Hi REVSYS team,

I saw that you offer Python and Django code reviews. I built PatchPilot to leave comments directly on changed lines in GitHub. You pick the model, and you can run inference locally with Ollama.

Would you pick one public PR for me to review in a fork? I’ll send you the thread, including any findings I think the bot got wrong. I’d love to hear which comments, if any, you would have left for the author yourself.

Aman

## Turn a reply into a pilot

1. Agree on a repo, a reviewer and three upcoming PRs. Record how the reviewer handles a first pass today.
2. After each PR, ask them to mark comments as useful, incorrect or redundant. Record whether a finding led to a code or test change.
3. After the third PR, ask whether they want to leave PatchPilot installed. If setup or ongoing support is the obstacle, ask what they would pay to have that handled. Quote a defined service only after learning the scope.
4. Track replies, first completed reviews, second-week usage and paid commitments separately. An install alone is a weak buying signal.

## Public launch draft

**Show HN: PatchPilot – GitHub PR reviews with your own model or local Ollama**

I built PatchPilot for the moment when a PR is ready and its reviewer is still working on something else. It reads the diff, posts inline comments and lets the author ask for an explanation in the same thread. It runs with a provider key or a self-hosted Ollama instance.

I’d like feedback on the comments you would delete. Did the model miss context, repeat what the code already said, or flag something worth changing?

Use the [runnable repository](https://github.com/89Aman/PatchPilot) as the submission URL. Before posting, add a public review thread from a repo you control so readers can see the output without installing anything. Follow the [Show HN guidelines](https://news.ycombinator.com/showhn.html).

## Measure search after release

Inspect the production homepage in Google Search Console and submit the existing sitemap at /sitemap.xml. Save a baseline of impressions and clicks for searches mentioning GitHub PR review, bring-your-own-key code review and Ollama pull request review. Compare after four weeks. Ask each pilot how they found the app so search visits and customer conversations can be evaluated together. [Google Search Central](https://developers.google.com/search/docs/fundamentals/seo-starter-guide)
