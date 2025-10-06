## Goal
The goal for this task is to ultimately set up a funding arbitrage 

But instead of coding straight away, I want to do some research, both in utilizing existing funding rate arb repositories which i will clone and add to this repo, as well as the current status quo of the exchanges supported by my repoistory 

Firstly, read the current projects @ARCHITECTURE.md to get a better understanding of the system design of my repo first

The goal for this is 3 fold:
- create a new strategy that we can implement under /strategies
- utilize them to create a delta neutral + funding fee arbitrage & farming bot
- farm the platforms "points" system which is the biggest financial gain and motivation here

I want to firstly go ahead and look through existing repositories, to capture crucial functionalities of existing funding rate arb repoistories, to integrate into my current repository

## Funding arbitrage, what is it?
In the world of perpetual futures, funding rates are like a seesaw between long and short positions. These rates are periodic payments that help keep the perpetual price in sync with the spot price. But here's where the fun begins: funding rates can differ across various DEXs, creating opportunities for savvy traders to play the arbitrage game.

Picture this: DEX A has a positive funding rate, while DEX B has a negative one. This means that on DEX A, longs are paying shorts, and on DEX B, shorts are paying longs. It's like a funding rate tug-of-war!

To seize this arbitrage opportunity, you'll want to go short on DEX A (where the funding rate is higher) and long on DEX B (where the funding rate is lower). This way, you'll be collecting the higher funding rate on your short position while paying the lower funding rate on your long position. It's a delightful dance of funding rates that can lead to profitable arbitrage if executed correctly.

## Motivation
As stated above, one of my biggest motivations here is to be able to farm the platform's points system, while maintaining a delta neutral (and slightly profitable) delta neutral funding arbitrage strategy


## Main Dex's to work with

The main Dex's which I want to farm are (from highest to lowest):
- lighter
- edgex
- paradex
- grvt

Additional dexs which are big and have a lot of liquididty and assumingly best fundings are:
- hyperliquid

The goal here is to farm as much as possible for lighter dex, with others having slightly lesser priorrity.


---

## Tasks

So, based on all the context above, some tasks we have to firstly determine is
- what might be some algorithms that we can use to efficiently find funding arb
- will it be better to split this funding arb "finding" into a microservice, which this service / repositoruy / strategy can call, to basically give better separation of concerns?
- what are some crucial functions that are important to funding rate arb?


## Existing repos

under /existing-repos, there are 2 projects which i have cloned. these are the highest starred github repositories i could find

for the 2 projects, they are:
- cex-funding-rate-arbitrage (specific to centralised exchanges, binance etc)
- dex-funding-rate-arb (focuses on dex's, Apex DEX etc)

the goal here is to extract out crucial information that will prove necessary and helpful to my integration plan to create my own fuding arb strategy for my repostory
