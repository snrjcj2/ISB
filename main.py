import asyncio
import io
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import aiohttp
import discord
import matplotlib.pyplot as plt
from discord import app_commands
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True

# Toggle this: True for global sync (test), False for guild-only (production)
USE_GLOBAL_SYNC = True  # Set to False after testing

# Your Discord user ID for restricted commands
AUTHORIZED_USER_ID = 1459581008025227518

# Optional: if you ever set USE_GLOBAL_SYNC=False, set this env var
GUILD_ID = int(os.environ.get("GUILD_ID", "0") or "0")


@dataclass(frozen=True)
class PageResult:
    items: List[Dict[str, Any]]
    next_cursor: Optional[str]


class ISBBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self) -> None:
        timeout = aiohttp.ClientTimeout(total=20, connect=10, sock_read=15)
        self.http_session = aiohttp.ClientSession(timeout=timeout)

        try:
            if USE_GLOBAL_SYNC:
                synced = await self.tree.sync()
                print(f"Successfully synced {len(synced)} command(s) globally.")
            else:
                if not GUILD_ID:
                    raise RuntimeError("USE_GLOBAL_SYNC=False but GUILD_ID env var is not set.")
                guild = discord.Object(id=GUILD_ID)
                synced = await self.tree.sync(guild=guild)
                print(f"Successfully synced {len(synced)} command(s) to guild {guild.id}.")
        except Exception as e:
            print(f"Command sync failed: {e}")

    async def close(self) -> None:
        try:
            if self.http_session and not self.http_session.closed:
                await self.http_session.close()
        finally:
            await super().close()


bot = ISBBot()


def _fmt_dt_utc(dt: Optional[datetime]) -> str:
    if not dt:
        return "Not available"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _iso_to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Roblox often returns ISO strings with trailing 'Z'
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except Exception:
        return None


async def _http_json(url: str, *, params: Optional[dict] = None) -> Dict[str, Any]:
    assert bot.http_session is not None
    headers = {
        "User-Agent": "ISB-IntelligenceBot/1.0",
        "Accept": "application/json",
    }
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with bot.http_session.get(url, params=params, headers=headers) as resp:
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", "1") or "1")
                    await asyncio.sleep(min(5.0, retry_after) + (0.2 * attempt))
                    continue
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status} for {url}: {text[:200]}")
                return await resp.json()
        except Exception as e:
            last_exc = e
            await asyncio.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"Request failed after retries: {last_exc}")


async def _paged(url: str, *, params: Optional[dict] = None, limit: int = 100) -> Iterable[Dict[str, Any]]:
    cursor: Optional[str] = None
    params = dict(params or {})
    params.setdefault("limit", limit)
    while True:
        if cursor:
            params["cursor"] = cursor
        data = await _http_json(url, params=params)
        for item in data.get("data", []) or []:
            yield item
        cursor = data.get("nextPageCursor")
        if not cursor:
            break

# --- Roblox API Functions ---

async def get_group_members_with_ranks(group_id: int) -> Dict[str, Dict[str, Any]] | str:
    members: Dict[str, Dict[str, Any]] = {}
    url = f"https://groups.roblox.com/v1/groups/{group_id}/users"
    try:
        async for user_data in _paged(url, params={"sortOrder": "Asc"}):
            user = user_data.get("user") or {}
            username = user.get("username") or user.get("name") or "Unknown"
            role = user_data.get("role") or {}
            members[username] = {
                "rank_name": role.get("name", "Unknown"),
                "rank_num": role.get("rank", "Unknown"),
            }
        return members
    except Exception as e:
        return f"Unable to retrieve group {group_id} members: {e}"


async def get_user_groups(user_id: int) -> List[Dict[str, Any]] | str:
    url = f"https://groups.roblox.com/v1/users/{user_id}/groups"
    groups: List[Dict[str, Any]] = []
    try:
        async for group in _paged(url):
            g = group.get("group") or {}
            r = group.get("role") or {}
            groups.append(
                {
                    "name": g.get("name", "Unknown"),
                    "rank": r.get("name", "Unknown"),
                    "rank_num": r.get("rank", "Unknown"),
                }
            )
        return groups
    except Exception as e:
        return f"Unable to retrieve groups: {e}"


async def _count(url: str) -> int:
    data = await _http_json(url)
    return int(data.get("count", 0) or 0)


async def get_user_profile(user_id: int) -> Dict[str, Any] | str:
    try:
        user_data = await _http_json(f"https://users.roblox.com/v1/users/{user_id}")
        username = user_data.get("name", "Unknown")
        display_name = user_data.get("displayName", "Unknown")
        description = user_data.get("description") or "No description"
        created_iso = user_data.get("created")
        created_dt = _iso_to_dt(created_iso)
        account_age_days = (datetime.now(timezone.utc) - created_dt).days if created_dt else None

        friends_count, followers_count, following_count = await asyncio.gather(
            _count(f"https://friends.roblox.com/v1/users/{user_id}/friends/count"),
            _count(f"https://friends.roblox.com/v1/users/{user_id}/followers/count"),
            _count(f"https://friends.roblox.com/v1/users/{user_id}/followings/count"),
        )

        # Quick badge list for embeds (names only, limited)
        badge_names: List[str] = []
        async for badge in _paged(f"https://badges.roblox.com/v1/users/{user_id}/badges"):
            name = badge.get("name")
            if name:
                badge_names.append(name)
            if len(badge_names) >= 50:
                break

        total_badges = len(badge_names)  # (fast estimate; full count may be higher)

        risk_score = 0
        risk_factors: List[str] = []
        if account_age_days is not None and account_age_days < 365:
            risk_score += 2
            risk_factors.append("Recent account (< 1 year)")
        if friends_count < 10:
            risk_score += 1
            risk_factors.append("Low friends (< 10)")
        if followers_count < 50:
            risk_score += 1
            risk_factors.append("Low followers (< 50)")
        if total_badges < 5:
            risk_score += 1
            risk_factors.append("Few badges (< 5)")

        risk_level = "Low" if risk_score <= 1 else "Medium" if risk_score <= 3 else "High"
        return {
            "username": username,
            "display_name": display_name,
            "description": description,
            "created_iso": created_iso or "Unknown",
            "created_dt": created_dt,
            "account_age_days": account_age_days if account_age_days is not None else "Unknown",
            "friends_count": friends_count,
            "followers_count": followers_count,
            "following_count": following_count,
            "total_badges_estimate": total_badges,
            "badges_list": badge_names[:20],
            "risk_level": risk_level,
            "risk_factors": risk_factors,
        }
    except Exception as e:
        print(f"Profile fetch error for user {user_id}: {e}")
        return "Unable to retrieve profile data. Please check the User ID."


async def _get_user_badge_ids(user_id: int) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    async for badge in _paged(f"https://badges.roblox.com/v1/users/{user_id}/badges"):
        bid = badge.get("id")
        name = badge.get("name", "Unknown")
        if isinstance(bid, int):
            out.append((bid, name))
    return out


async def get_user_badges_awarded_dates(user_id: int) -> List[Dict[str, Any]] | str:
    """
    Uses:
      https://badges.roblox.com/v1/users/{UserID}/badges/awarded-dates?badgeIds=1,2,3
    """
    try:
        badge_id_name = await _get_user_badge_ids(user_id)
        if not badge_id_name:
            return []

        id_to_name = {bid: name for bid, name in badge_id_name}
        badge_ids = [bid for bid, _ in badge_id_name]

        results: List[Dict[str, Any]] = []
        chunk_size = 100
        for i in range(0, len(badge_ids), chunk_size):
            chunk = badge_ids[i : i + chunk_size]
            joined = ",".join(str(x) for x in chunk)
            url = f"https://badges.roblox.com/v1/users/{user_id}/badges/awarded-dates"
            data = await _http_json(url, params={"badgeIds": joined})
            for item in data.get("data", []) or []:
                badge_id = item.get("badgeId")
                awarded = _iso_to_dt(item.get("awardedDate"))
                if isinstance(badge_id, int):
                    results.append({"id": badge_id, "name": id_to_name.get(badge_id, "Unknown"), "date": awarded})
        return results
    except Exception as e:
        print(f"Badges awarded-dates fetch error for user {user_id}: {e}")
        return "Unable to retrieve badge awarded dates. Please check the User ID."


async def _collect_usernames_from_friends_endpoint(url: str, *, max_users: int = 1000) -> Tuple[set, bool]:
    names: set = set()
    truncated = False
    async for item in _paged(url):
        name = item.get("name") or item.get("username")
        if name:
            names.add(name)
        if len(names) >= max_users:
            truncated = True
            break
    return names, truncated


async def compare_users(user_id_1: int, user_id_2: int) -> Dict[str, Any] | str:
    """
    Compares:
    - Common friends
    - Common followers (bounded for performance)
    - Common groups
    """
    try:
        friends_url_1 = f"https://friends.roblox.com/v1/users/{user_id_1}/friends"
        friends_url_2 = f"https://friends.roblox.com/v1/users/{user_id_2}/friends"
        followers_url_1 = f"https://friends.roblox.com/v1/users/{user_id_1}/followers"
        followers_url_2 = f"https://friends.roblox.com/v1/users/{user_id_2}/followers"

        (friends_1, friends_1_tr), (friends_2, friends_2_tr) = await asyncio.gather(
            _collect_usernames_from_friends_endpoint(friends_url_1, max_users=2000),
            _collect_usernames_from_friends_endpoint(friends_url_2, max_users=2000),
        )
        common_friends = friends_1 & friends_2

        (followers_1, fol_1_tr), (followers_2, fol_2_tr) = await asyncio.gather(
            _collect_usernames_from_friends_endpoint(followers_url_1, max_users=1500),
            _collect_usernames_from_friends_endpoint(followers_url_2, max_users=1500),
        )
        common_followers = followers_1 & followers_2
        followers_truncated = fol_1_tr or fol_2_tr

        groups_1, groups_2 = await asyncio.gather(get_user_groups(user_id_1), get_user_groups(user_id_2))
        if isinstance(groups_1, str) or isinstance(groups_2, str):
            return "Unable to retrieve group data for comparison. Please check the User IDs."
        common_groups = {g["name"] for g in groups_1} & {g["name"] for g in groups_2}

        # Threat scoring based on intersection density (bounded sets)
        commonality_score = len(common_friends) + len(common_followers) + len(common_groups)
        threat_level = "Low" if commonality_score < 5 else "Medium" if commonality_score <= 15 else "High"
        return {
            "common_friends": sorted(common_friends),
            "common_followers": sorted(common_followers),
            "common_groups": sorted(common_groups),
            "threat_level": threat_level,
            "followers_truncated": followers_truncated,
            "notes": "Follower comparison is bounded for performance." if followers_truncated else None,
        }
    except Exception as e:
        print(f"Comparison error for users {user_id_1} and {user_id_2}: {e}")
        return "Unable to perform comparison. Please check the User IDs."

# --- Discord Commands ---

class GroupCheckView(discord.ui.View):
    def __init__(self, pages, current_page=0):
        super().__init__(timeout=300)
        self.pages = pages
        self.current_page = current_page
        self.update_buttons()

    def update_buttons(self):
        self.children[0].disabled = self.current_page == 0  # Previous
        self.children[1].disabled = self.current_page == len(self.pages) - 1  # Next

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.grey)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.grey)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

@bot.tree.command(name="group_check", description="Check users in both Roblox groups and show their ranks.")
@app_commands.describe(group_id_1="Group - 1", group_id_2="Group - 2")
async def group_check(interaction: discord.Interaction, group_id_1: int, group_id_2: int):
    await interaction.response.defer()
    members_1 = await get_group_members_with_ranks(group_id_1)
    if isinstance(members_1, str):
        embed = discord.Embed(title="Error", description="Unable to retrieve group data. Please check the Group IDs.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    members_2 = await get_group_members_with_ranks(group_id_2)
    if isinstance(members_2, str):
        embed = discord.Embed(title="Error", description="Unable to retrieve group data. Please check the Group IDs.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    intersection = set(members_1.keys()) & set(members_2.keys())
    if not intersection:
        embed = discord.Embed(title="No Common Users", description="No users are in both groups.", color=0x808080)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    intersection_list = list(intersection)
    pages = []
    per_page = 5
    for i in range(0, len(intersection_list), per_page):
        page_users = intersection_list[i:i + per_page]
        description = ""
        for username in page_users:
            rank_1 = members_1[username]
            rank_2 = members_2[username]
            description += f"**{username}**\n- Rank in {group_id_1}: {rank_1['rank_name']} ({rank_1['rank_num']})\n- Rank in {group_id_2}: {rank_2['rank_name']} ({rank_2['rank_num']})\n\n"
        embed = discord.Embed(title=f"Common Users in Groups {group_id_1} and {group_id_2}", description=description, color=0x808080)
        embed.add_field(name="Total Common Users", value=str(len(intersection_list)), inline=True)
        embed.set_footer(text="Information extracted from ISB database.")
        pages.append(embed)
    view = GroupCheckView(pages)
    await interaction.followup.send(embed=pages[0], view=view)

@bot.tree.command(name="profile_analysis", description="Advanced Roblox profile check for ALT risk analysis.")
@app_commands.describe(user_id="Roblox User ID")
async def profile_analysis(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer()
    profile = await get_user_profile(user_id)
    if isinstance(profile, str):
        embed = discord.Embed(title="Error", description=profile, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    threat_level = profile["risk_level"]
    color = 0x00FF00 if threat_level == "Low" else 0xFFA500 if threat_level == "Medium" else 0xFF0000
    embed = discord.Embed(title=f"Profile Analysis: {profile['username']}", color=color)
    embed.add_field(name="Display Name", value=profile['display_name'], inline=True)
    embed.add_field(name="Description", value=profile['description'][:200], inline=False)
    embed.add_field(name="Join Date", value=profile['created_iso'], inline=True)
    embed.add_field(name="Account Age", value=f"{profile['account_age_days']} days", inline=True)
    embed.add_field(name="Friends", value=str(profile['friends_count']), inline=True)
    embed.add_field(name="Followers", value=str(profile['followers_count']), inline=True)
    embed.add_field(name="Following", value=str(profile['following_count']), inline=True)
    embed.add_field(name="Total Badges (estimate)", value=str(profile['total_badges_estimate']), inline=True)
    embed.add_field(name="Badges", value=', '.join(profile['badges_list']) or 'None', inline=False)
    embed.add_field(name="Threat Level", value=threat_level, inline=True)
    embed.add_field(name="Risk Factors", value=', '.join(profile['risk_factors']) if profile['risk_factors'] else 'None', inline=False)
    embed.set_footer(text="Information extracted from ISB database.")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="compare_users", description="Compare common friends, followers, and groups between two Roblox users.")
@app_commands.describe(user_id_1="First Roblox User ID", user_id_2="Second Roblox User ID")
async def compare_users_command(interaction: discord.Interaction, user_id_1: int, user_id_2: int):
    await interaction.response.defer()
    comparison = await compare_users(user_id_1, user_id_2)
    if isinstance(comparison, str):
        embed = discord.Embed(title="Error", description=comparison, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    threat_level = comparison["threat_level"]
    color = 0x00FF00 if threat_level == "Low" else 0xFFA500 if threat_level == "Medium" else 0xFF0000
    embed = discord.Embed(title=f"User Comparison: {user_id_1} vs {user_id_2}", color=color)
    embed.add_field(name="Common Friends", value=', '.join(comparison['common_friends'][:50]) or 'None', inline=False)
    embed.add_field(name="Common Followers", value=', '.join(comparison['common_followers'][:50]) or 'None', inline=False)
    embed.add_field(name="Common Groups", value=', '.join(comparison['common_groups'][:50]) or 'None', inline=False)
    embed.add_field(name="Threat Level", value=threat_level, inline=True)
    if comparison.get("notes"):
        embed.add_field(name="Notes", value=comparison["notes"], inline=False)
    embed.set_footer(text="Information extracted from ISB database.")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="profile_intel", description="Detailed Roblox profile inspector with groups, badges, and account details.")
@app_commands.describe(user_id="Roblox User ID")
async def profile_intel(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer()
    profile = await get_user_profile(user_id)
    if isinstance(profile, str):
        embed = discord.Embed(title="Error", description=profile, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    groups = await get_user_groups(user_id)
    if isinstance(groups, str):
        embed = discord.Embed(title="Error", description="Unable to retrieve group data. Please check the User ID.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    threat_level = profile["risk_level"]
    color = 0x00FF00 if threat_level == "Low" else 0xFFA500 if threat_level == "Medium" else 0xFF0000
    embed = discord.Embed(title=f"Profile Intel: {profile['username']}", color=color)
    embed.add_field(name="Display Name", value=profile['display_name'], inline=True)
    embed.add_field(name="Account Creation Date", value=profile['created_iso'], inline=True)
    embed.add_field(name="Account Age", value=f"{profile['account_age_days']} days", inline=True)
    embed.add_field(name="Friends", value=str(profile['friends_count']), inline=True)
    embed.add_field(name="Followers", value=str(profile['followers_count']), inline=True)
    embed.add_field(name="Following", value=str(profile['following_count']), inline=True)
    embed.add_field(name="Total Badges (estimate)", value=str(profile['total_badges_estimate']), inline=True)
    embed.add_field(name="Badges List", value=', '.join(profile['badges_list']) or 'None', inline=False)
    groups_str = '\n'.join([f"- {g['name']} | {g['rank']} ({g['rank_num']})" for g in groups[:10]]) or 'None'
    embed.add_field(name="Groups", value=groups_str, inline=False)
    embed.add_field(name="Past Usernames", value="Not available via API", inline=False)
    embed.add_field(name="Threat Level", value=threat_level, inline=True)
    embed.add_field(name="Risk Factors", value=', '.join(profile['risk_factors']) if profile['risk_factors'] else 'None', inline=False)
    embed.set_footer(text="Information extracted from ISB database.")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="badge_info", description="Generate a badge graph for a Roblox user.")
@app_commands.describe(user_id="Roblox User ID")
async def badge_info(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer()
    profile = await get_user_profile(user_id)
    if isinstance(profile, str):
        embed = discord.Embed(title="Error", description=profile, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    badges = await get_user_badges_awarded_dates(user_id)
    if isinstance(badges, str):
        embed = discord.Embed(title="Error", description=badges, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    if not badges:
        embed = discord.Embed(title=f"Badge Info for {profile['username']}", description="No badges found.", color=0xC0C0C0)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    # Prepare data
    total_badges = len(badges)
    badges_with_dates = [b for b in badges if b["date"]]
    badges_without_dates = [b for b in badges if not b["date"]]
    description = f"**Total Badges:** {total_badges}\n"
    if badges_with_dates:
        description += "**Badges with Awarded Dates (sample):**\n" + "\n".join(
            [f"- {b['name']}: {b['date'].strftime('%Y-%m-%d')}" for b in sorted(badges_with_dates, key=lambda x: x["date"])[:20]]
        )
    if badges_without_dates:
        description += "\n**Badges without Dates:**\n" + "\n".join([f"- {b['name']}" for b in badges_without_dates[:20]])
    embed = discord.Embed(title=f"Badge Info for {profile['username']}", description=description, color=0xC0C0C0)
    if badges_with_dates:
        # Generate graph for badges with dates
        dates = sorted([b["date"].date() for b in badges_with_dates if b["date"]])
        cumulative = list(range(1, len(dates) + 1))
        plt.figure(figsize=(10, 6))
        plt.plot(dates, cumulative, marker='o', color='#C0C0C0', linewidth=2, markersize=5)
        plt.fill_between(dates, cumulative, color='#E5E5E5', alpha=0.5)
        plt.title(f"Badge Progression for {profile['username']}", fontsize=16, color='#808080')
        plt.xlabel('Awarded Date', fontsize=12, color='#808080')
        plt.ylabel('Cumulative Badge Count', fontsize=12, color='#808080')
        plt.xticks(rotation=45, color='#808080')
        plt.yticks(color='#808080')
        plt.grid(True, color='#D3D3D3', linestyle='--', alpha=0.7)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor='#F5F5F5')
        buf.seek(0)
        plt.close()
        file = discord.File(buf, 'badge_graph.png')
        embed.set_image(url="attachment://badge_graph.png")
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed, file=file)
    else:
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="tge_user_lookup", description="Lookup Discord user info by username or ID in this server.")
@app_commands.describe(user_input="Discord Username or User ID")
async def tge_user_lookup(interaction: discord.Interaction, user_input: str):
    await interaction.response.defer()
    try:
        guild = interaction.guild
        if not guild:
            embed = discord.Embed(
                title="Server Context Required",
                description="This command can only be used inside a server.",
                color=0xFF0000,
            )
            embed.set_footer(text="Information extracted from ISB database.")
            await interaction.followup.send(embed=embed)
            return

        member: Optional[discord.Member] = None
        if user_input.isdigit():
            uid = int(user_input)
            member = guild.get_member(uid)
            if not member:
                try:
                    member = await guild.fetch_member(uid)
                except discord.NotFound:
                    member = None
        else:
            # Try cache first
            needle = user_input.lower()
            member = discord.utils.find(
                lambda m: needle in (m.name or "").lower() or needle in (m.display_name or "").lower(),
                guild.members,
            )
            if not member:
                # Query members via API (fast, doesn't require full member cache)
                try:
                    matches = await guild.query_members(query=user_input, limit=10)
                    member = matches[0] if matches else None
                except Exception:
                    member = None

        if not member:
            embed = discord.Embed(title="User Not Found", description="User not found in this server. Ensure the username/ID is correct and the user is a member.", color=0xFF0000)
            embed.set_footer(text="Information extracted from ISB database.")
            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(title=f"Discord User Intel: {member}", color=0x00FF00)
        embed.add_field(name="User ID", value=str(member.id), inline=True)
        embed.add_field(name="Account Created", value=_fmt_dt_utc(member.created_at), inline=True)
        embed.add_field(name="Server Joined", value=_fmt_dt_utc(member.joined_at), inline=True)
        roles = [r.name for r in getattr(member, "roles", []) if r.name != "@everyone"]
        embed.add_field(name="Roles", value=", ".join(roles) if roles else "None", inline=False)
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"tge_user_lookup error: {e}")  # Log for debugging
        embed = discord.Embed(title="Error", description="Unable to retrieve user data. Please check the input.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="discord_user_lookup", description="Advanced Discord user lookup with optional server join date check.")
@app_commands.describe(user_input="Discord Username or User ID", server_id="Optional Server ID to check join date")
async def discord_user_lookup(interaction: discord.Interaction, user_input: str, server_id: str = None):
    await interaction.response.defer()
    try:
        target_user: Optional[discord.abc.User] = None
        if user_input.isdigit():
            uid = int(user_input)
            target_user = bot.get_user(uid)
            if not target_user:
                try:
                    target_user = await bot.fetch_user(uid)
                except discord.NotFound:
                    target_user = None
        else:
            needle = user_input.lower()
            for g in bot.guilds:
                m = discord.utils.find(
                    lambda mem: needle in (mem.name or "").lower() or needle in (mem.display_name or "").lower(),
                    g.members,
                )
                if m:
                    target_user = m
                    break

        if not target_user:
            embed = discord.Embed(title="User Not Found", description="User not found. Ensure the username/ID is correct and accessible.", color=0xFF0000)
            embed.set_footer(text="Information extracted from ISB database.")
            await interaction.followup.send(embed=embed)
            return
        embed = discord.Embed(title=f"Discord User Intel: {target_user}", color=0x00FF00)
        embed.add_field(name="User ID", value=str(target_user.id), inline=True)
        embed.add_field(name="Account Created", value=_fmt_dt_utc(target_user.created_at), inline=True)
        if server_id:
            try:
                guild = bot.get_guild(int(server_id))
                if guild:
                    member = guild.get_member(target_user.id)
                    if not member:
                        try:
                            member = await guild.fetch_member(target_user.id)
                        except discord.NotFound:
                            member = None
                    if member:
                        embed.add_field(name=f"Joined {guild.name}", value=_fmt_dt_utc(member.joined_at), inline=False)
                    else:
                        embed.add_field(name=f"Joined {guild.name}", value="User not in this server", inline=False)
                else:
                    embed.add_field(name="Server Join Date", value="Invalid server ID", inline=False)
            except ValueError:
                embed.add_field(name="Server Join Date", value="Invalid server ID format", inline=False)
        if getattr(target_user, "display_avatar", None):
            embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"discord_user_lookup error: {e}")  # Log for debugging
        embed = discord.Embed(title="Error", description="Unable to retrieve user data. Please check the input.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="calibrate_uplink", description="Calibrate uplink (admin only).")
async def calibrate_uplink(interaction: discord.Interaction):
    if interaction.user.id != AUTHORIZED_USER_ID:
        embed = discord.Embed(title="Unauthorized", description="Access denied.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if USE_GLOBAL_SYNC:
            synced = await bot.tree.sync()
            embed = discord.Embed(title="Uplink Calibrated", description=f"Successfully synced {len(synced)} command(s) globally.", color=0x00FF00)
        else:
            if not GUILD_ID:
                raise RuntimeError("GUILD_ID env var is not set.")
            guild = discord.Object(id=GUILD_ID)
            synced = await bot.tree.sync(guild=guild)
            embed = discord.Embed(title="Uplink Calibrated", description=f"Successfully synced {len(synced)} command(s) to the guild.", color=0x00FF00)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed, ephemeral=True)
        print(f"Manual sync: {len(synced)} commands synced.")
    except Exception as e:
        embed = discord.Embed(title="Sync Failed", description=f"Failed to sync commands: {e}", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed, ephemeral=True)
        print(f"Manual sync error: {e}")

# --- Run the bot ---
bot.run(os.environ.get('TOKEN'))

